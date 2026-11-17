# NixOS

NixOS flake configuration for the ZimaBoard (`x86_64-linux`), with an integrated Home Manager profile and a standalone user profile.

## Repository map

```text
hosts/zimaboard/          host NixOS configuration and hardware-specific modules
home/                     Luke's Home Manager profile and local installers
scripts/network_resilience/  Mac-side network checks and opt-in fault testing
tests/                    Python regression and Nix evaluation tests
docs/                     host, service, home-profile, and investigation guidance
```

This is one deployable Git repository: the host configuration, Home Manager profile, scripts, tests, and operations documentation evolve together. Project planning and accepted decisions live separately in the public [NixOS AAA repository](https://github.com/luketych/nixos--aaa). Nix declarations are the source of truth for system configuration; chezmoi separately owns portable Pi settings and is not applied by a NixOS rebuild.

## Host

- `zimaboard` — NixOS 26.05 on the 1 TB WD_BLACK NVMe.
- Tunarr runs in Docker at `http://<zimaboard-ip>:8000`; persistent app data is stored in `/var/lib/tunarr`.
- The live LAN address `192.168.4.66` currently comes from DHCP and the eero reservation. The prepared static NetworkManager profile is opt-in and has not been activated.
- Caddy serves PeerTube at `peertube.ktrx.cards` and proxies to `127.0.0.1:9000`.

## Deploy and validate

Start with the [ZimaBoard update procedure](docs/hosts/zimaboard/update.md). Run the static check from the repository root:

```sh
nix flake check --no-build
```

A normal NixOS rebuild also activates the integrated Home Manager profile. Review the full system-generation diff before changing network settings; do not do the first static-network cutover over an unattended SSH session. The [networking guide](docs/hosts/zimaboard/networking.md) and [resilience test guide](docs/hosts/zimaboard/network-resilience-tests.md) explain the safeguards and live-test boundaries.

## Documentation

- [ZimaBoard update and activation](docs/hosts/zimaboard/update.md)
- [Static LAN configuration and cutover](docs/hosts/zimaboard/networking.md)
- [Opt-in network resilience tests](docs/hosts/zimaboard/network-resilience-tests.md)
- [Pi CLI and chezmoi profile](docs/home/pi-cli.md)
- [PeerTube ingress diagnosis](docs/services/peertube-ingress.md)
- [Tunarr deployment](docs/services/tunarr.md)
- [Known issues and resolutions](docs/known_issues.md)
- [Dated reports and evidence](docs/reports/)

## Safety boundary

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository. Pi credentials, sessions, caches, and other mutable application state are not copied from the Mac or managed here.