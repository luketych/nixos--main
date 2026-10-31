source_dir="${CHEZMOI_SOURCE_DIR:-$HOME/.local/share/chezmoi/main_repo}"
target="$HOME/.pi/agent"
runtime_dir="${PI_MANAGED_RUNTIME_DIR:-$HOME/.local/share/pi-managed}"

show_help() {
  cat <<'HELP'
chezmoi-pi: inspect or apply only the Pi agent configuration

Usage:
  chezmoi-pi diff
  chezmoi-pi apply

Environment:
  CHEZMOI_SOURCE_DIR      Chezmoi source root (default: ~/.local/share/chezmoi/main_repo)
  PI_MANAGED_RUNTIME_DIR  Nix-managed Pi runtime (default: ~/.local/share/pi-managed)
HELP
}

require_source() {
  if [ ! -d "$source_dir/private_dot_pi/private_agent" ]; then
    printf 'error: Pi agent source files are missing at %s\n' "$source_dir" >&2
    printf 'initialize it with: chezmoi --source %q init <dotfiles-repo>\n' "$source_dir" >&2
    return 1
  fi
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
    chezmoi --source "$source_dir" diff --recursive "$target"
    ;;
  apply)
    require_source
    require_pi_runtime
    chezmoi --source "$source_dir" apply "$target"
    ;;
  *)
    printf 'error: unknown command: %s\n\n' "$command_name" >&2
    show_help >&2
    exit 2
    ;;
esac
