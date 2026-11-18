#!/usr/bin/env python3
"""Confirm and apply a ZimaBoard desktop composition from the local checkout."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

CHECKOUT = Path("/home/luke/nixos")
HOST = "zimaboard"
CURRENT_SYSTEM = Path("/run/current-system")
RECORD = "etc/nixos-cli/composition.json"
SELECTOR = "NIXOS_CLI_WINDOW_MANAGER"
MANAGERS = ("none", "sway", "niri", "hyprland")
VERSION = "1.0.0"


def active_composition():
    generation = CURRENT_SYSTEM.resolve(strict=True)
    if not (generation / "etc").is_dir():
        raise ValueError(f"Not an active NixOS generation: {generation}")
    record = generation / RECORD
    try:
        data = json.loads(record.read_text())
    except FileNotFoundError:
        if record.is_symlink():
            raise ValueError(f"Broken composition record: {record}") from None
        return None  # Generation predates the CLI.
    if (
        not isinstance(data, dict)
        or type(data.get("schema")) is not int
        or data["schema"] != 1
        or data.get("windowManager") not in MANAGERS
        or "revision" not in data
        or not isinstance(data["revision"], (str, type(None)))
    ):
        raise ValueError(f"Invalid or unsupported composition record: {record}")
    return data


def checkout_revision():
    def git(*args):
        return subprocess.check_output(["git", "-C", str(CHECKOUT), *args], text=True).strip()

    if Path(git("rev-parse", "--show-toplevel")) != CHECKOUT.resolve():
        raise ValueError(f"Expected a Git checkout rooted at {CHECKOUT}")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError(f"{CHECKOUT} is dirty; reconcile local changes before rebuilding.")
    return git("rev-parse", "HEAD")


def append_event(history, attempt, event, **fields):
    row = dict(attempt, event=event, timestamp=datetime.now(timezone.utc).isoformat(), **fields)
    history.write(json.dumps(row) + "\n")
    history.flush()
    os.fsync(history.fileno())


def rebuild(manager, history_path):
    if os.geteuid() == 0:
        raise ValueError("Run nixos_cli as Luke, not with sudo; activation requests sudo itself.")
    revision = checkout_revision()
    preserve = manager is None
    if preserve:
        active = active_composition()
        manager = active["windowManager"] if active else "none"

    command = [
        "nixos-rebuild", "switch", "--sudo", "--flake", f"{CHECKOUT}#{HOST}",
        "--impure", "--no-update-lock-file",
    ]
    print(f"Target: {CHECKOUT}#{HOST}\nCheckout: {revision}\nTiling session: {manager} (KDE stays available)")
    print(f"Command: {SELECTOR}={manager} {shlex.join(command)}")
    try:
        answer = input("Switch this ZimaBoard now? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in {"y", "yes"}:
        print("Cancelled; no rebuild or history entry.")
        return 0

    # Another invocation may have switched during the prompt. Lock before
    # rechecking the confirmed target and keep it locked until switch ends.
    history_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as history:
        try:
            fcntl.flock(history, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another nixos_cli rebuild is in progress.") from None
        if checkout_revision() != revision:
            raise ValueError("Checkout changed during confirmation; review and try again.")
        if preserve and active_composition() != active:
            raise ValueError("Active composition changed during confirmation; review and try again.")

        attempt = {"id": uuid.uuid4().hex, "host": HOST, "revision": revision, "windowManager": manager}
        append_event(history, attempt, "started")
        error = None
        try:
            code = subprocess.run(command, cwd=CHECKOUT, env={**os.environ, SELECTOR: manager}).returncode
            status = 128 - code if code < 0 else code
            if status == 0:
                record = active_composition()
                if not record or (record["windowManager"], record["revision"]) != (manager, revision):
                    raise ValueError("Switch returned zero but the active composition did not match the request.")
        except KeyboardInterrupt:
            status, error = 130, "Interrupted"
        except (OSError, ValueError) as exc:
            status, error = 1, str(exc)
        append_event(history, attempt, "finished", exitStatus=status, **({"error": error} if error else {}))
    if status:
        print(f"nixos_cli: {error or f'Rebuild exited with status {status}'}. Check --status before retrying.", file=sys.stderr)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage the ZimaBoard's local NixOS configuration.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", "--apply-current", action="store_true", help="Confirm and rebuild with the active selection (default: none).")
    action.add_argument("--window-manager", nargs="?", choices=MANAGERS, action="append",
                        metavar="MANAGER", help="List choices, or confirm and switch to one manager.")
    action.add_argument("--status", action="store_true", help="Show the active generation's composition.")
    action.add_argument("--log", action="store_true", help="Print rebuild-attempt history (JSON Lines, oldest first).")
    action.add_argument("--version", action="version", version=f"nixos_cli {VERSION}")
    args = parser.parse_args(argv)
    if args.window_manager and len(args.window_manager) != 1:
        parser.error("--window-manager may only be supplied once")
    if args.window_manager == [None]:
        print(" ".join(MANAGERS))
        return 0
    if not any(vars(args).values()):
        parser.print_help()
        return 0

    state_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    history_path = state_dir / "nixos_cli/history.jsonl"
    try:
        if args.status:
            record = active_composition()
            if record:
                print(f"Active tiling session: {record['windowManager']}\nRevision: {record['revision'] or 'unknown'}")
            else:
                print("Active tiling session: none (generation predates composition recording)")
            return 0
        if args.log:
            try:
                with history_path.open(encoding="utf-8") as history:
                    for line in history:
                        print(line, end="")
            except FileNotFoundError:
                print("No rebuild attempts recorded.")
            return 0
        return rebuild(args.window_manager[0] if args.window_manager else None, history_path)
    except KeyboardInterrupt:
        print("\nnixos_cli: Interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"nixos_cli: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
