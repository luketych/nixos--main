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
caffeinate -i rsync -a --no-owner --no-group --exclude='.git/' ./ luke@192.168.4.73:/home/luke/nixos/
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
`~/.local/bin`. `pi0` and `pi1` lazily seed isolated settings from `pi` and share
its login and sessions. `pi1` additionally enables `bigpowers` and `ponytail`.

Chezmoi owns the portable Pi configuration under `~/.pi/agent`. It is
intentionally not initialized or applied by a NixOS rebuild. After arranging
GitHub access for the private dotfiles repository, initialize its source and
review the Pi-only diff:

```sh
chezmoi --source ~/.local/share/chezmoi/main_repo init git@github.com:luketych/chezmoi-repo--main.git
chezmoi-pi diff
chezmoi-pi apply
```

To activate Luke's user environment without a privileged NixOS rebuild, run
Home Manager standalone from the ZimaBoard checkout:

```sh
nix run github:nix-community/home-manager/release-26.05 -- switch --flake /home/luke/nixos#luke
```

`chezmoi-pi apply` checks that the Nix-managed Pi runtimes are present before
applying only `~/.pi/agent`. Start `pi` afterward and authenticate on the
ZimaBoard. Pi credentials, sessions, caches, and other mutable application state
are not copied from the Mac or managed by chezmoi.

The Pi settings intentionally disable audible idle notifications on non-macOS systems; the Mac keeps its existing system sound. A few custom resources may still be host-specific, so review `chezmoi-pi diff` before applying.

## PeerTube ingress

Caddy serves `peertube.ktrx.cards` with automatic HTTPS and reverse-proxies to
PeerTube on `127.0.0.1:9000`. The home router must forward TCP ports 80 and 443
to the ZimaBoard at `192.168.4.73`; public DNS/proxy settings must reach that
origin. PeerTube RTMP uses TCP 1935 separately if livestream ingest is needed.

## Secrets and application data

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository.
