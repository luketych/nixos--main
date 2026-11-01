#!/usr/bin/env bash
set -euo pipefail

: "${PI_PACKAGE:?PI_PACKAGE is required}"
: "${PI_VERSION:?PI_VERSION is required}"
: "${PI_NPM_BIN:?PI_NPM_BIN is required}"
: "${PI_NODE_BIN:?PI_NODE_BIN is required}"

prefix="${PI_PREFIX:-$HOME/.local}"
cli_root="${PI_CLI_ROOT:-$HOME/.local/share/pi-managed/pi-cli}"
version_key="$(printf '%s' "$PI_VERSION" | tr -c 'A-Za-z0-9._-' '_')"
version_dir="$cli_root/$version_key"
package_json="$version_dir/lib/node_modules/$PI_PACKAGE/package.json"
version_bin="$version_dir/bin/pi"
main_bin="$prefix/bin/pi0"

package_version() {
  local package_file="$1"
  [ -r "$package_file" ] || return 0
  "$PI_NODE_BIN" -e '
    try {
      const value = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8")).version;
      if (typeof value === "string") process.stdout.write(value);
    } catch {}
  ' "$package_file" 2>/dev/null || true
}

installed_version="$(package_version "$package_json")"
if [ "$installed_version" != "$PI_VERSION" ] || [ ! -x "$version_bin" ]; then
  mkdir -p "$cli_root"
  staging="$cli_root/.${version_key}.tmp.$$"
  rm -rf -- "$staging"
  trap 'rm -rf -- "$staging"' EXIT

  printf 'Installing Pi coding agent %s to versioned prefix %s\n' "$PI_VERSION" "$version_dir"
  "$PI_NPM_BIN" install -g --ignore-scripts --min-release-age=0 \
    --prefix "$staging" --no-fund --no-audit "$PI_PACKAGE@$PI_VERSION"

  staged_module="$staging/lib/node_modules/$PI_PACKAGE"
  staged_version="$(package_version "$staged_module/package.json")"
  if [ "$staged_version" != "$PI_VERSION" ] || [ ! -x "$staging/bin/pi" ]; then
    printf 'error: staged Pi install is incomplete or has unexpected version: %s\n' "$staged_version" >&2
    exit 1
  fi

  if [ -e "$version_dir" ]; then
    displaced="$cli_root/.${version_key}.displaced.$(date +%s).$$"
    mv "$version_dir" "$displaced"
    printf 'Preserved incomplete Pi tree at %s\n' "$displaced" >&2
  fi
  mv "$staging" "$version_dir"
  trap - EXIT
fi

mkdir -p "$prefix/bin"
main_tmp="$prefix/bin/.pi0.tmp.$$"
rm -f -- "$main_tmp"
cat > "$main_tmp" <<EOF
#!/usr/bin/env bash
set -euo pipefail

export PI_CODING_AGENT_DIR="\$HOME/.pi/agent"
unset PI_CODING_AGENT_SESSION_DIR
export PI_SUBAGENT_PI_COMMAND="$main_bin"
exec "$version_bin" "\$@"
EOF
chmod 755 "$main_tmp"
mv -f "$main_tmp" "$main_bin"

# pi0 uses the full main profile. pi/plain stays sparse; pi1 inherits resources.
# Both isolated instances share login metadata and sessions with the main profile.
write_instance_launcher() {
  local name="$1"
  local instance_name="$2"
  local bin="$prefix/bin/$name"
  local tmp="$prefix/bin/.$name.tmp.$$"
  local managed_packages=""

  case "$name" in
    pi1) managed_packages="npm:bigpowers npm:@dietrichgebert/ponytail" ;;
  esac

  rm -f -- "$tmp"
  cat > "$tmp" <<EOF
#!/usr/bin/env bash
set -euo pipefail

main_dir="\$HOME/.pi/agent"
instance_dir="\$HOME/.pi/instances/$instance_name"
managed_packages="$managed_packages"

mkdir -p "\$instance_dir"

# Chezmoi owns plain settings. Do not seed main settings/resources into it, and
# do not delete any resources that already exist in an older plain profile.
if [ "$instance_name" != "plain" ]; then
  if [ ! -e "\$instance_dir/settings.json" ] && [ ! -L "\$instance_dir/settings.json" ] && [ -r "\$main_dir/settings.json" ]; then
    cp -p "\$main_dir/settings.json" "\$instance_dir/settings.json"
  fi

  # Chezmoi may seed pi1 before its first launch. Fill missing baseline keys
  # without replacing its existing preferences or package list.
  if [ -r "\$main_dir/settings.json" ] && [ -r "\$instance_dir/settings.json" ] && [ ! -L "\$instance_dir/settings.json" ]; then
    "$PI_NODE_BIN" - "\$main_dir/settings.json" "\$instance_dir/settings.json" <<'NODE'
const fs = require("node:fs");
const { isDeepStrictEqual } = require("node:util");
const [mainPath, instancePath] = process.argv.slice(2);
const mainSettings = JSON.parse(fs.readFileSync(mainPath, "utf8"));
const instanceSettings = JSON.parse(fs.readFileSync(instancePath, "utf8"));
if (!mainSettings || typeof mainSettings !== "object" || Array.isArray(mainSettings)
    || !instanceSettings || typeof instanceSettings !== "object" || Array.isArray(instanceSettings)) {
  throw new Error("Pi settings must be JSON objects");
}
const mergedSettings = { ...mainSettings, ...instanceSettings };
if (!isDeepStrictEqual(mergedSettings, instanceSettings)) {
  fs.writeFileSync(instancePath, JSON.stringify(mergedSettings, null, 2) + "\\n");
}
NODE
  fi

  if [ ! -d "\$instance_dir/agents" ] && [ -d "\$main_dir/agents" ]; then
    mkdir -p "\$instance_dir/agents"
    for agent in "\$main_dir"/agents/*.md; do
      [ -e "\$agent" ] || continue
      cp -p "\$agent" "\$instance_dir/agents/"
    done
  fi

  for file in APPEND_SYSTEM.md SYSTEM.md pi-vcc-config.json; do
    if [ ! -e "\$instance_dir/\$file" ] && [ ! -L "\$instance_dir/\$file" ] && [ -r "\$main_dir/\$file" ]; then
      cp -p "\$main_dir/\$file" "\$instance_dir/\$file"
    fi
  done

  # Keep extension imports valid from the deeper instance directory.
  if [ -L "\$instance_dir/extensions" ]; then
    rm -f "\$instance_dir/extensions"
  fi
  if [ ! -e "\$instance_dir/extensions" ] && [ -d "\$main_dir/extensions" ]; then
    mkdir -p "\$instance_dir/extensions"
  fi
  if [ -d "\$instance_dir/extensions" ] && [ -d "\$main_dir/extensions" ]; then
    for extension in "\$main_dir"/extensions/*; do
      [ -e "\$extension" ] || continue
      ext_name="\${extension##*/}"
      target="\$instance_dir/extensions/\$ext_name"
      case "\$ext_name" in
        pi-subagents.ts)
          if [ ! -e "\$target" ] && [ ! -L "\$target" ]; then
            cat > "\$target" <<'LOADER'
// Runtime source is pinned and installed by Nix/Home Manager.
// This loader is four levels below HOME in an instance extension directory.
export { default } from "../../../../.local/share/pi-managed/pi-subagents/src/index.ts";
LOADER
          fi
          ;;
        pi-vcc.ts)
          if [ ! -e "\$target" ] && [ ! -L "\$target" ]; then
            cat > "\$target" <<'LOADER'
// Runtime source is pinned and installed by Nix/Home Manager.
// This loader is four levels below HOME in an instance extension directory.
export { default } from "../../../../.local/share/pi-managed/pi-vcc/index.ts";
LOADER
          fi
          ;;
        *)
          if [ ! -e "\$target" ] && [ ! -L "\$target" ]; then
            ln -s "\$extension" "\$target"
          fi
          ;;
      esac
    done
  fi

  for dir in skills prompts themes resource-groups system-templates; do
    if [ ! -e "\$instance_dir/\$dir" ] && [ ! -L "\$instance_dir/\$dir" ] && [ -d "\$main_dir/\$dir" ]; then
      ln -s "\$main_dir/\$dir" "\$instance_dir/\$dir"
    fi
  done
fi

if [ ! -e "\$instance_dir/auth.json" ] && [ ! -L "\$instance_dir/auth.json" ]; then
  ln -s "\$main_dir/auth.json" "\$instance_dir/auth.json"
fi

if [ ! -e "\$instance_dir/models-store.json" ] && [ ! -L "\$instance_dir/models-store.json" ] && { [ -e "\$main_dir/models-store.json" ] || [ -L "\$main_dir/models-store.json" ]; }; then
  ln -s "\$main_dir/models-store.json" "\$instance_dir/models-store.json"
fi

if [ -n "\$managed_packages" ] && [ -r "\$instance_dir/settings.json" ]; then
  "${PI_NODE_BIN}" - "\$instance_dir/settings.json" \$managed_packages <<'NODE'
const fs = require("node:fs");
const [settingsPath, ...managedPackages] = process.argv.slice(2);
const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
const currentPackages = Array.isArray(settings.packages) ? settings.packages : [];
let changed = !Array.isArray(settings.packages);
for (const packageSpec of managedPackages) {
  if (!currentPackages.includes(packageSpec)) {
    currentPackages.push(packageSpec);
    changed = true;
  }
}
if (changed) {
  settings.packages = currentPackages;
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\\n");
}
NODE
fi

export PI_CODING_AGENT_DIR="\$instance_dir"
export PI_CODING_AGENT_SESSION_DIR="\$main_dir/sessions"
export PI_SUBAGENT_PI_COMMAND="$bin"
exec "$version_bin" "\$@"
EOF
  chmod 755 "$tmp"
  mv -f "$tmp" "$bin"
}

write_instance_launcher pi plain
write_instance_launcher pi1 pi1
