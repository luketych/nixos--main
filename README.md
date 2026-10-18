# NixOS

NixOS flake configuration for the ZimaBoard (`x86_64-linux`).

## Hosts

- `zimaboard` — NixOS 26.05 on the 1 TB WD_BLACK NVMe.

## Apply the configuration

From this repository on the ZimaBoard:

```sh
sudo nixos-rebuild switch --flake .#zimaboard
```

The host configuration imports its generated `hardware-configuration.nix`; review that file carefully before changing partitioning or filesystem UUIDs.

## Secrets and application data

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository.
