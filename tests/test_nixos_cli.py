import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import nixos_cli as cli


class CLITests(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.generation = tmp / "generation"
        (self.generation / "etc/nixos-cli").mkdir(parents=True)
        self.current = tmp / "current-system"
        self.current.symlink_to(self.generation)
        self.record = self.generation / cli.RECORD
        self.history = tmp / "nixos_cli/history.jsonl"
        self.enterContext(patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp), cli.SELECTOR: "hyprland"}))
        self.enterContext(patch.object(cli, "CURRENT_SYSTEM", self.current))
        self.enterContext(patch.object(cli.os, "geteuid", return_value=1000))
        self.revision = self.enterContext(patch.object(cli, "checkout_revision", return_value="new"))
        self.run_rebuild = self.enterContext(patch.object(cli.subprocess, "run"))
        self.run_rebuild.side_effect = self.switch

    def set_active(self, manager, revision="old"):
        self.record.write_text(json.dumps({"schema": 1, "windowManager": manager, "revision": revision}))

    def switch(self, command, *, cwd, env):
        self.assertEqual(command, ["nixos-rebuild", "switch", "--sudo", "--flake", "/home/luke/nixos#zimaboard",
                                   "--impure", "--no-update-lock-file"])
        self.assertEqual(cwd, Path("/home/luke/nixos"))
        self.set_active(env[cli.SELECTOR], "new")
        return subprocess.CompletedProcess(command, 0)

    def call(self, *args, answer="yes"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("builtins.input", return_value=answer, side_effect=EOFError if answer is None else None) as prompt:
            try:
                status = cli.main(args)
            except SystemExit as exc:
                status = exc.code
        self.prompt = prompt
        return status, stdout.getvalue(), stderr.getvalue()

    def events(self):
        return [json.loads(line) for line in self.history.read_text().splitlines()]

    def test_info_and_parser_do_not_rebuild(self):
        for args, text in [((), "usage:"), (("--help",), "usage:"), (("--version",), "nixos_cli"),
                           (("--window-manager",), "none sway niri hyprland"), (("--log",), "No rebuild")]:
            with self.subTest(args=args):
                code, stdout, _ = self.call(*args)
                self.assertEqual(code, 0)
                self.assertIn(text, stdout)
        for args in [("--window-manager", "list"), ("--window-manager", "invalid"),
                     ("--window-manager", "sway,niri"), ("--window-manager", "sway", "niri"),
                     ("--window-manager", "sway", "--window-manager", "sway"),
                     ("--window-manager", "sway", "--run")]:
            with self.subTest(args=args):
                self.assertEqual(self.call(*args)[0], 2)
                self.prompt.assert_not_called()
        self.run_rebuild.assert_not_called()
        self.assertFalse(self.history.exists())

    def test_confirmation_required(self):
        for args in [("--run",), ("--apply-current",), ("--window-manager", "sway")]:
            for answer in ("", "n", "maybe", None):
                with self.subTest(args=args, answer=answer):
                    self.assertIn("Cancelled", self.call(*args, answer=answer)[1])
                    self.prompt.assert_called_once()
        self.run_rebuild.assert_not_called()
        self.assertFalse(self.history.exists())

    def test_selection_reapply_and_reset(self):
        self.assertIn("predates", self.call("--status")[1])
        self.assertEqual(self.call("--run", answer=" Y ")[0], 0)
        self.assertEqual(self.call("--window-manager", "niri")[0], 0)
        self.assertIn("niri", self.call("--status")[1])
        self.assertEqual(self.call("--apply-current")[0], 0)
        self.assertEqual(self.call("--window-manager", "none")[0], 0)
        results = [ev for ev in self.events() if ev["event"] == "finished"]
        self.assertEqual([ev["windowManager"] for ev in results], ["none", "niri", "niri", "none"])
        self.assertTrue(all(ev["exitStatus"] == 0 for ev in results))

    def test_failure_and_mismatched_activation_never_log_success(self):
        self.set_active("sway")
        self.run_rebuild.side_effect = None
        for rebuild_status, expected in [(42, 42), (0, 1), (-15, 143)]:
            with self.subTest(rebuild_status=rebuild_status):
                self.run_rebuild.return_value.returncode = rebuild_status
                self.assertEqual(self.call("--window-manager", "niri")[0], expected)
                start, finish = self.events()[-2:]
                self.assertEqual(start["id"], finish["id"])
                self.assertEqual(finish["exitStatus"], expected)
                self.assertIn("sway", self.call("--status")[1])
        self.assertEqual(self.call("--log")[1], self.history.read_text())

    def test_status_follows_current_generation_not_history(self):
        self.assertEqual(self.call("--window-manager", "niri")[0], 0)
        previous = self.current.parent / "previous"
        (previous / "etc/nixos-cli").mkdir(parents=True)
        (previous / cli.RECORD).write_text('{"schema":1,"windowManager":"sway","revision":"previous"}')
        self.current.unlink()
        self.current.symlink_to(previous)
        self.assertIn("sway", self.call("--status")[1])
        self.assertIn("niri", self.call("--log")[1])

    def test_broken_record_blocks_reapply_but_explicit_reset_can_recover(self):
        self.record.write_text('{"schema":1,"windowManager":["sway","niri"],"revision":null}')
        self.assertEqual(self.call("--status")[0], 1)
        self.assertEqual(self.call("--apply-current")[0], 1)
        self.run_rebuild.assert_not_called()
        self.assertEqual(self.call("--window-manager", "none")[0], 0)

    def test_checkout_must_stay_reviewed(self):
        self.revision.side_effect = ["new", "changed"]
        self.assertIn("Checkout changed", self.call("--run")[2])
        self.run_rebuild.assert_not_called()
        self.assertEqual(self.history.read_text(), "")


@unittest.skipUnless(shutil.which("nix"), "Nix is needed for composition evaluation")
class NixCompositionTests(unittest.TestCase):
    def test_session_catalog_and_record(self):
        repo = Path(__file__).resolve().parents[1]
        for value, args in [("sway", []), *((name, ["--impure"]) for name in cli.MANAGERS),
                            ("sway,niri", ["--impure"])]:
            with self.subTest(value=value, args=args):
                result = subprocess.run([
                    "nix", "eval", "--json", "--no-update-lock-file", *args,
                    ".#nixosConfigurations.zimaboard.config", "--apply", '''c: {
                      sessions = c.services.displayManager.sessionData.sessionNames;
                      inherit (c.services.displayManager.sddm) enable;
                      record = builtins.fromJSON c.environment.etc."nixos-cli/composition.json".text;
                    }''',
                ], cwd=repo, env={**os.environ, cli.SELECTOR: value}, capture_output=True, text=True)
                if value not in cli.MANAGERS:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("local.zimaboard.windowManager", result.stderr)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                active = value if args else "none"
                data = json.loads(result.stdout)
                self.assertEqual(data["record"]["windowManager"], active)
                self.assertTrue(data["enable"])
                sessions = {name.removesuffix("-uwsm") for name in data["sessions"]}
                self.assertIn("plasma", sessions)
                self.assertEqual(sessions.intersection(cli.MANAGERS), set() if active == "none" else {active})


if __name__ == "__main__":
    unittest.main()
