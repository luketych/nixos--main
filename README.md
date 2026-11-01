# NixOS

NixOS flake configuration for the ZimaBoard (`x86_64-linux`).

## Hosts

- `zimaboard` — NixOS 26.05 on the 1 TB WD_BLACK NVMe.

## Update the ZimaBoard

Edit and push changes from the Mac checkout at `/Users/luketych/_/Dev/alpha/nixos`:

```sh
cd /Users/luketych/_/Dev/alpha/nixos
nix flake check --no-build
git status
git add <changed-files>
git commit -m "Describe the change"
git push
# Use the board's current SSH address; DHCP addresses can change.
caffeinate -i rsync -a --no-owner --no-group --exclude='.git/' ./ luke@192.168.4.75:/home/luke/nixos/
```

Then, in a shell on the ZimaBoard, apply the synced checkout:

```sh
sudo nixos-rebuild switch --flake /home/luke/nixos#zimaboard
```

Review `git status` before committing, and never add secrets or application data. The host configuration imports its generated `hardware-configuration.nix`; review it carefully before changing partitioning or filesystem UUIDs.

## Chezmoi and Pi coding agent

Home Manager installs `chezmoi`, Node.js 24, and the Pi coding agent CLI
(`@earendil-works/pi-coding-agent` 1.4.0). The flake exposes both the
integrated NixOS configuration and the standalone `homeConfigurations.luke`
profile. Nix also provides pinned
`pi-subagents` and `pi-vcc` runtimes under `~/.local/share/pi-managed`. The Pi
CLI and runtime versions are declared in `home/luke.nix`. Home Manager puts
`pi`, `pi0`, and `pi1` on the user profile `PATH`, backed by launchers in
`~/.local/bin`, using the same profile mapping as the Mac:

| Command | Agent directory | Behavior |
| --- | --- | --- |
| `pi` | `~/.pi/agent` | Full chezmoi-owned main profile |
| `pi0` | `~/.pi/instances/pi0` | Sparse isolated profile; main settings/resources are not seeded |
| `pi1` | `~/.pi/instances/pi1` | Inherited resources and instance-specific preferences |

Both instances share the main profile's login/model metadata and sessions.
`pi1` fills missing settings keys from main without replacing instance overrides,
and additionally enables `bigpowers` and `ponytail`. Existing pi0 custom
settings/resources are preserved, and the old `~/.pi/instances/plain` directory
is unmanaged and left untouched. Model preferences are owned by chezmoi, not
hard-coded in these launchers.

Chezmoi owns the portable Pi configuration and the main/pi0/pi1 settings.
It is intentionally not initialized or applied by a NixOS rebuild. Use the
coordinated corrective source containing the main, pi0, and pi1 modifiers. The
earlier [chezmoi PR #23](https://github.com/luketych/chezmoi-repo--main/pull/23) and
[Mac launcher PR #48](https://github.com/luketych/nix-darwin--main/pull/48)
introduced the incorrect reversed/plain layout; they are historical context,
not the desired mapping. Review the matching corrective PRs before activation.
After arranging access to the private dotfiles repository, initialize its source:

```sh
chezmoi --source ~/.local/share/chezmoi/main_repo init git@github.com:luketych/chezmoi-repo--main.git
```

If source files were copied rather than cloned, do not assume that directory is
a Git checkout or run `git pull` there. Preserve it and obtain a separate reviewed
source tree. The helper accepts either form via `CHEZMOI_SOURCE_DIR`, but refuses
an outdated source missing any of the three settings modifiers.

Activate the Nix-managed launchers/runtime first. Then review and apply the
settings in two phases, with the main settings and shared coder definition first:

```sh
source_dir="$HOME/.local/share/chezmoi/main_repo"
mkdir -p "$HOME/.pi/agent/agents" "$HOME/.pi/instances/pi0" "$HOME/.pi/instances/pi1"
chezmoi --source "$source_dir" diff \
  "$HOME/.pi/agent/settings.json" "$HOME/.pi/agent/agents/coder.md"
# Proceed only after reviewing the full diff.
chezmoi --source "$source_dir" apply \
  "$HOME/.pi/agent/settings.json" "$HOME/.pi/agent/agents/coder.md"
chezmoi --source "$source_dir" diff \
  "$HOME/.pi/instances/pi0/settings.json" "$HOME/.pi/instances/pi1/settings.json"
# Review this second diff against the updated on-disk main settings.
chezmoi --source "$source_dir" apply \
  "$HOME/.pi/instances/pi0/settings.json" "$HOME/.pi/instances/pi1/settings.json"
```

The coordinated chezmoi defaults are `gpt-6-luna` / `medium`; the shared coder
uses `openai-codex/gpt-6-luna` / `high`. Existing pi1 agent files are copies made
at first use and are **not** refreshed automatically. Review an existing
`~/.pi/instances/pi1/agents/coder.md` separately before updating its route; do not
blindly replace custom agent prompts or the entire agents directory.

To activate Luke's user environment without a privileged NixOS rebuild, run
Home Manager standalone from the ZimaBoard checkout:

```sh
nix run github:nix-community/home-manager/release-26.05 -- switch --flake /home/luke/nixos#luke
```

`chezmoi-pi diff` covers main/pi0/pi1; `chezmoi-pi apply` checks the Nix-managed
runtime, creates target parents, applies the full managed main profile, then the
pi0/pi1 profiles. It is **not** a settings-only operation. For missing instances,
a combined diff can preview seeds from the old on-disk main baseline, whereas
apply updates main first; use the explicit two-phase commands above instead.

Start fresh sessions afterward and verify the defaults; existing sessions are
not migrated. Authenticate on the ZimaBoard if needed. Pi credentials, sessions,
caches, and other mutable application state are not copied from the Mac or
managed by chezmoi.

The Pi settings disable audible idle notifications when initially configured on
Linux; existing user-selected idle-notify preferences are preserved by the merged
modifier. The Mac keeps its existing system sound. A few custom resources may
still be host-specific, so review every scoped diff before applying.

### Pi regression checks

The direct-shell checks require Bash, Python 3, Node.js, `jq`, and ShellCheck;
real chezmoi is needed only for the optional paired test. The installed
`chezmoi-pi` wrapper supplies its own `jq` through Nix, even when `jq` is not on
the interactive shell's PATH.

```sh
bash -n home/install-pi-cli.sh home/chezmoi-pi.sh
shellcheck -S error home/install-pi-cli.sh
shellcheck -S error -s bash home/chezmoi-pi.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

These tests use fake npm/CLI fixtures and real Node, never download packages or
run inference, and use only temporary homes with synthetic mutable state. To
also exercise real chezmoi against a reviewed source checkout:

```sh
PI_TEST_CHEZMOI_SOURCE="$HOME/.local/share/chezmoi/main_repo" \
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The optional paired check copies only settings modifiers and the shared coder
definition, not credentials or live application state. These checks validate
routing/settings wiring, not the complete Pi SDK or provider connectivity.

## PeerTube ingress

Caddy serves `peertube.ktrx.cards` with automatic HTTPS and reverse-proxies to
PeerTube on `127.0.0.1:9000`. The home router must forward TCP ports 80 and 443
to the ZimaBoard at `192.168.4.73`; public DNS/proxy settings must reach that
origin. PeerTube RTMP uses TCP 1935 separately if livestream ingest is needed.

## Secrets and application data

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository.
