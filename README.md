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
caffeinate -i rsync -a --no-owner --no-group --exclude='.git/' ./ luke@192.168.4.36:/home/luke/nixos/
```

Then, in a shell on the ZimaBoard, apply the synced checkout:

```sh
sudo nixos-rebuild switch --flake /home/luke/nixos#zimaboard
```

Review `git status` before committing, and never add secrets or application data. The host configuration imports its generated `hardware-configuration.nix`; review it carefully before changing partitioning or filesystem UUIDs.

## Secrets and application data

This repository is for operating-system configuration. Do not commit PeerTube environment files, credentials, SSH private/host keys, or application data. PeerTube's deployment and data currently live under `/DATA` and `/shared`, outside this repository.
