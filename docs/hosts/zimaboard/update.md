# Update the ZimaBoard

Develop on the Mac in a feature worktree based on `origin/main`. Review and merge a PR before updating the host. From the feature worktree, stage new flake sources first (Nix ignores untracked files), then validate:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover
nix flake check --no-build
```

## Sync reviewed commits, not the Mac working tree

On the ZimaBoard, as `luke`:

```sh
cd /home/luke/nixos
git status --short --branch
```

**Stop if this is not a clean `main`.** In particular, do not rsync, reset, or overwrite the host's local Tunarr deployment. Reconcile `README.md`, `hosts/zimaboard/configuration.nix`, and the new `docs/services/tunarr.md` via a separate reviewed Tunarr PR first. If those exact changes are already in reviewed commits, inspect the local diffs against those commits (including the untracked doc), save the local tree with `git stash push --include-untracked -m 'ZimaBoard Tunarr before sync'`, and retain the stash as a backup. Do not pop those same changes on top of their merged copies. If they differ, back them up and reconcile the differences before proceeding. Never commit application data or credentials.

When `main` is clean and includes a path to the required Tunarr changes, pull and review:

```sh
git pull --ff-only origin main
git log -1 --oneline
nix flake check --no-build
```

Check that required services, including the `/shared/tunarr-media:/media:ro` mount if Tunarr is deployed, are still declared before switching. Pulling does not activate anything. Do not edit the checkout, pull, or run another rebuild while a switch is in progress.

## Rebuild locally with `nixos_cli`

Use the checkout command for initial activation and after pulling CLI updates; it works before the new CLI is installed. Keep the TV or an SSH/TTY recovery shell available and save any open graphical work before confirming.

```sh
cd /home/luke/nixos
python3 scripts/nixos_cli.py --apply-current
```

Once installed, use `nixos_cli` as Luke, **not** with `sudo`. The CLI targets `/home/luke/nixos#zimaboard`; `nixos-rebuild switch --sudo` asks for privilege during activation. It requires a clean checkout, prints the target, revision and selection, and switches only after `y` or `yes`. It does not pull, update `flake.lock`, or choose a Git branch for you.

```sh
nixos_cli --window-manager        # list: none sway niri hyprland
nixos_cli --window-manager sway   # request one alternate session
nixos_cli --status                # active generation, not the last attempted switch
nixos_cli --run                   # same as --apply-current: keep active selection
nixos_cli --log                   # JSON Lines rebuild history
nixos_cli --version
```

`none` means KDE-only and is the default (also used when bootstrapping from an older generation with no record). A selected manager adds an alternate session to SDDM; it does not replace KDE or force a session at login. The CLI passes the single value to an impure Nix evaluation with a pinned lock file. The selected value is saved **in the activated system generation**, not Git or the CLI history. A later Git pull cannot change it; `--apply-current` carries it forward when switching to new commits. A plain `nixos-rebuild switch --flake /home/luke/nixos#zimaboard` does **not** preserve the selection: it evaluates the KDE-only default.

`--log` reads `$XDG_STATE_HOME/nixos_cli/history.jsonl` (default `~/.local/state/nixos_cli/history.jsonl`). A confirmed attempt logs `started` and then `finished` with the exit status. A start without a finish means the outcome wasn't recorded, not success. Direct rebuilds and rollbacks aren't in this log. `--status` reads the live system's record at `/run/current-system/etc/nixos-cli/composition.json`; it reports the installed composition, not your currently logged-in session.

## TV smoke test and recovery

1. From a terminal on the ZimaBoard, run `nixos_cli --window-manager sway` and confirm. Check `nixos_cli --status` reports `sway`.
2. Log out on the TV. In SDDM, select Sway, log in, open a terminal, and verify `echo "$XDG_CURRENT_DESKTOP"`. Sign out, select Plasma, and log into KDE again.
3. To return to KDE-only, select `none` with the CLI and confirm. Check `--status`, then verify SDDM no longer offers Sway. Leave the tested selection active instead if desired.

If an alternate session fails, select Plasma at SDDM. From an SSH or TTY shell you can run `nixos_cli --window-manager none` (or the checkout command) to explicitly reset the composition. If activation itself fails, check `--status` and `--log`; do not assume a failed switch left the active system untouched. To roll back the full system configuration rather than just the manager, use `sudo nixos-rebuild switch --rollback` or boot the previous working generation. A rollback restores the selection of that generation as well and can also roll back unrelated services. Before rebuilding again from Git, check that the checkout still declares the intended services.

A rebuild also activates integrated Home Manager. The standalone profile can be activated separately; see the [Pi and chezmoi guide](../../home/pi-cli.md). Review hardware and network changes separately before any cutover.
