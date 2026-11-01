source_dir="${CHEZMOI_SOURCE_DIR:-$HOME/.local/share/chezmoi/main_repo}"
main_target="$HOME/.pi/agent"
instance_targets=(
  "$HOME/.pi/instances/plain"
  "$HOME/.pi/instances/pi1"
)
targets=("$main_target" "${instance_targets[@]}")
runtime_dir="${PI_MANAGED_RUNTIME_DIR:-$HOME/.local/share/pi-managed}"

show_help() {
  cat <<'HELP'
chezmoi-pi: inspect or apply Pi configuration for main, plain, and pi1

Usage:
  chezmoi-pi diff
  chezmoi-pi apply

Environment:
  CHEZMOI_SOURCE_DIR      Chezmoi source root (default: ~/.local/share/chezmoi/main_repo)
  PI_MANAGED_RUNTIME_DIR  Nix-managed Pi runtime (default: ~/.local/share/pi-managed)

Notes:
  apply covers the full managed main profile, not just settings.json.
  Main settings are applied before instance settings. For missing instances,
  review/apply main settings first: a combined diff uses the old on-disk baseline.
HELP
}

require_source() {
  local modifier
  for modifier in \
    private_dot_pi/private_agent/modify_settings.json \
    private_dot_pi/instances/private_plain/modify_settings.json \
    private_dot_pi/instances/private_pi1/modify_settings.json; do
    if [ ! -f "$source_dir/$modifier" ]; then
      printf 'error: coordinated Pi settings source is missing: %s\n' "$source_dir/$modifier" >&2
      printf 'refresh the merged chezmoi source before inspecting or applying Pi profiles\n' >&2
      return 1
    fi
  done
}

require_pi_runtime() {
  local manifest="$runtime_dir/manifest.json"
  if [ ! -r "$manifest" ] \
    || [ ! -r "$runtime_dir/pi-subagents/src/index.ts" ] \
    || [ ! -r "$runtime_dir/pi-vcc/index.ts" ] \
    || ! jq -e '
      .cli.package == "@earendil-works/pi-coding-agent"
      and .cli.version == "1.4.0"
      and .extensions["pi-subagents"].version == "2.9.1"
      and .extensions["pi-vcc"].version == "0.7.2"
    ' "$manifest" >/dev/null; then
    printf 'error: Nix-managed Pi runtime is missing or has unexpected versions at %s\n' "$runtime_dir" >&2
    printf 'apply the NixOS configuration first, then retry\n' >&2
    return 1
  fi
}

command_name="${1:-help}"
case "$command_name" in
  help|--help|-h)
    show_help
    ;;
  diff)
    require_source
    chezmoi --source "$source_dir" diff --recursive "${targets[@]}"
    ;;
  apply)
    require_source
    require_pi_runtime
    mkdir -p "${targets[@]}"
    chezmoi --source "$source_dir" apply "$main_target"
    chezmoi --source "$source_dir" apply "${instance_targets[@]}"
    ;;
  *)
    printf 'error: unknown command: %s\n\n' "$command_name" >&2
    show_help >&2
    exit 2
    ;;
esac
