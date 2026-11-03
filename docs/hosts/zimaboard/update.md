# Update the ZimaBoard

Edit and push changes from the Mac checkout at `/Users/luketych/_/Dev/alpha/nixos/main_repo`:

```sh
cd /Users/luketych/_/Dev/alpha/nixos/main_repo
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

A NixOS rebuild also activates the integrated Home Manager profile. The standalone Home Manager profile can be activated separately; see the [Pi and chezmoi guide](../../home/pi-cli.md).