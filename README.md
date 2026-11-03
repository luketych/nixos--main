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
# Enter the live address (.66 is currently verified via DHCP reservation).
printf 'Current ZimaBoard IP: '
read -r ZIMABOARD_IP
caffeinate -i rsync -a --no-owner --no-group --exclude='.git/' ./ "luke@${ZIMABOARD_IP:?Enter the current IP}:/home/luke/nixos/"
```

Then, in a shell on the ZimaBoard, apply the synced checkout:

```sh
sudo nixos-rebuild switch --flake /home/luke/nixos#zimaboard
```

Review `git status` before committing, and never add secrets or application data. The host configuration imports its generated `hardware-configuration.nix`; review it carefully before changing partitioning or filesystem UUIDs.

## Static LAN networking

The default configuration keeps NetworkManager DHCP and relies on the eero
reservation. `hosts/zimaboard/networking.nix` also defines an **opt-in,
disabled-by-default** static configuration for `192.168.4.66/22` on MAC
`00:e0:4c:5b:96:3e`. When explicitly enabled, it provides a preferred static
profile, a manual DHCP recovery profile, and a spare-port management rule.
Without that opt-in, it declares none of those settings or profiles.

The live address is `192.168.4.66` via DHCP. The prepared static profiles have
not been activated: having `.66` does not by itself mean IPv4 is configured
manually. Before opting in to static addressing, verify the reservation, check
for address conflicts, review the system-generation diff, and arrange recovery
access. See
[`docs/networking.md`](docs/networking.md) for provenance, build-only checks,
and the console-assisted test/rollback procedure. Do not run an unattended
remote system switch for the first cutover.

### Network resilience test group

The opt-in controller in `scripts/network_resilience/` runs from the Mac. Its
smoke mode is read-only; DHCP/static fault injection, reboot, and whole-system
rollback tests require explicit selection and safety acknowledgements. Start
with a non-disruptive check of the board's current DHCP address (the SSH
`zimaboard` alias must reference its already-trusted host key):

```sh
python3 -m scripts.network_resilience --host 192.168.4.66 --host-key-alias zimaboard
```

See [`docs/network-resilience-tests.md`](docs/network-resilience-tests.md) for
separate DHCP/static phases, reports, watchdog behavior, and manual checks that
cannot be automated. Fault injection is not part of normal unittest discovery.
The [2026-11-03 investigation report](docs/reports/2026-11-03-lan-resilience.md)
records live results, evidence limits, operating expectations, and follow-up options.

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
to the ZimaBoard's active LAN address (currently `192.168.4.66` via DHCP);
public DNS/proxy settings must reach that origin. NixOS does not update the eero
reservation or port forwards. PeerTube RTMP uses TCP 1935 separately if
livestream ingest is needed.

**Troubleshooting:** NixOS generates `/etc/caddy/caddy_config` and overrides the
packaged unit's `/etc/caddy/Caddyfile` command. A missing `Caddyfile` is not by
itself an error. See [Caddy configuration and public-ingress diagnosis](docs/peertube-ingress.md)
for the source of truth, effective-unit inspection, and checks that distinguish
local Caddy health from Cloudflare/router reachability.

## Secrets and application data

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository.
