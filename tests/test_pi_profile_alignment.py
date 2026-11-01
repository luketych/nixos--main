#!/usr/bin/env python3
"""Regression coverage for the ZimaBoard Pi profile shell scripts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "home/install-pi-cli.sh"
CHEZMOI_PI = REPO / "home/chezmoi-pi.sh"
PACKAGE = "@earendil-works/pi-coding-agent"
VERSIONS = ("1.4.0", "1.5.0-test")

FAKE_NPM = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import sys
args = sys.argv[1:]
prefix = Path(args[args.index("--prefix") + 1])
package, version = args[-1].rsplit("@", 1)
module = prefix / "lib/node_modules" / package
module.mkdir(parents=True)
(module / "package.json").write_text(json.dumps({"version": version}))
cli = prefix / "bin/pi"
cli.parent.mkdir(parents=True, exist_ok=True)
cli.write_text("""#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print(""" + repr(version) + """)
elif "--print-pi-env" in sys.argv:
    print(json.dumps({key: os.environ.get(key, "") for key in (
        "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR", "PI_SUBAGENT_PI_COMMAND"
    )}))
""")
cli.chmod(0o755)
with open(__import__("os").environ["FAKE_NPM_LOG"], "a") as log:
    log.write(version + "\n")
'''


class PiProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-profile-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.npm = self.root / "fake-npm"
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to execute the installer's embedded JavaScript")
        self.npm.write_text(FAKE_NPM)
        self.npm.chmod(0o755)
        self.npm_log = self.root / "npm.log"

    def install(self, version="1.4.0"):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "PI_PACKAGE": PACKAGE,
            "PI_VERSION": version,
            "PI_NPM_BIN": str(self.npm),
            "PI_NODE_BIN": str(self.node),
            "PI_PREFIX": str(self.home / ".local"),
            "PI_CLI_ROOT": str(self.home / ".local/share/pi-managed/pi-cli"),
            "FAKE_NPM_LOG": str(self.npm_log),
        }
        subprocess.run(["bash", "-euo", "pipefail", str(INSTALLER)], env=env, check=True)

    def invoke(self, launcher, *args):
        return subprocess.check_output(
            [str(launcher), *args], env={**os.environ, "HOME": str(self.home)}, text=True
        ).strip()

    def test_launchers_route_main_pi0_sparse_and_pi1_inherits_idempotently(self):
        main = self.home / ".pi/agent"
        main.mkdir(parents=True)
        main_settings = {
            "defaultProvider": "openai-codex", "defaultModel": "main-model",
            "defaultThinkingLevel": "high", "packages": ["npm:user-package"],
            "agents": ["agents"], "prompts": ["prompts"],
            "extensions": ["extensions"], "skills": ["skills"],
            "themes": ["themes"], "idleNotify": {"enabled": True},
        }
        (main / "settings.json").write_text(json.dumps(main_settings) + "\n")
        for resource in ("agents", "prompts", "extensions", "skills", "themes", "resource-groups"):
            (main / resource).mkdir()
            (main / resource / "sentinel").write_text(resource)
        for name in ("APPEND_SYSTEM.md", "SYSTEM.md", "pi-vcc-config.json"):
            (main / name).write_text("main " + name)
        (main / "auth.json").write_text('{"synthetic":true}\n')
        (main / "models-store.json").write_text('{"synthetic":true}\n')
        (main / "sessions").mkdir()
        (main / "sessions/sentinel").write_text("session")

        # Existing pi0 custom state and unmanaged legacy plain data are preserved.
        pi0_dir = self.home / ".pi/instances/pi0"
        pi0_dir.mkdir(parents=True)
        (pi0_dir / "sentinel").write_text("leave alone")
        plain_dir = self.home / ".pi/instances/plain"
        plain_dir.mkdir(parents=True)
        (plain_dir / "settings.json").write_text("legacy plain settings")
        old_tree = self.home / ".local/share/pi-managed/pi-cli/0.8.0/lib/node_modules/old"
        old_tree.mkdir(parents=True)
        (old_tree / "sentinel").write_text("old version")
        self.install()

        bin_dir = self.home / ".local/bin"
        pi, pi0, pi1 = (bin_dir / name for name in ("pi", "pi0", "pi1"))
        self.assertEqual(self.invoke(pi, "--print-pi-env"), json.dumps({
            "PI_CODING_AGENT_DIR": str(main), "PI_CODING_AGENT_SESSION_DIR": "",
            "PI_SUBAGENT_PI_COMMAND": str(pi),
        }))
        self.assertEqual(self.invoke(pi0, "--print-pi-env"), json.dumps({
            "PI_CODING_AGENT_DIR": str(pi0_dir),
            "PI_CODING_AGENT_SESSION_DIR": str(main / "sessions"),
            "PI_SUBAGENT_PI_COMMAND": str(pi0),
        }))

        self.assertFalse((pi0_dir / "settings.json").exists(), "pi0 must not seed main settings")
        self.assertFalse((pi0_dir / "agents").exists(), "pi0 must not seed main resources")
        seeded_resources = ("agents", "prompts", "extensions", "skills", "themes",
                            "APPEND_SYSTEM.md", "SYSTEM.md", "resource-groups",
                            "system-templates", "pi-vcc-config.json")
        self.assertTrue(all(not (pi0_dir / item).exists() for item in seeded_resources))
        self.assertEqual((pi0_dir / "auth.json").resolve(), (main / "auth.json").resolve())
        self.assertEqual((pi0_dir / "models-store.json").resolve(), (main / "models-store.json").resolve())
        existing_pi0 = {"defaultModel": "pi0-model", "packages": ["npm:pi0"], "custom": True}
        for item in seeded_resources:
            path = pi0_dir / item
            if item == "agents":
                path.mkdir()
                (path / "custom.md").write_text("custom agent")
            elif item in ("prompts", "extensions", "skills", "themes", "resource-groups", "system-templates"):
                path.mkdir()
                (path / "custom").write_text("custom")
            else:
                path.write_text("custom")
        (pi0_dir / "settings.json").write_text(json.dumps(existing_pi0) + "\n")
        self.invoke(pi0, "--version")
        self.assertEqual(json.loads((pi0_dir / "settings.json").read_text()), existing_pi0)
        for item in seeded_resources:
            self.assertTrue((pi0_dir / item).exists(), f"pi0 removed existing {item}")
        self.assertEqual((plain_dir / "settings.json").read_text(), "legacy plain settings")

        pi1_dir = self.home / ".pi/instances/pi1"
        pi1_dir.mkdir(parents=True)
        customization = pi1_dir / "agents"
        customization.mkdir()
        (customization / "custom.md").write_text("user-owned agent")
        pi1_overrides = {"defaultModel": "pi1-model", "defaultThinkingLevel": "low",
                         "custom": "pi1", "packages": ["npm:user-package", "npm:own"]}
        (pi1_dir / "settings.json").write_text(json.dumps(pi1_overrides) + "\n")
        for _ in range(2):
            self.invoke(pi1, "--version")
        settings = json.loads((pi1_dir / "settings.json").read_text())
        self.assertEqual(settings, {
            **main_settings, **pi1_overrides,
            "packages": ["npm:user-package", "npm:own", "npm:bigpowers", "npm:@dietrichgebert/ponytail"],
        })
        self.assertEqual((customization / "custom.md").read_text(), "user-owned agent")
        self.assertEqual((pi1_dir / "auth.json").resolve(), (main / "auth.json").resolve())
        self.assertEqual((pi1_dir / "models-store.json").resolve(), (main / "models-store.json").resolve())
        self.assertEqual((pi0_dir / "sentinel").read_text(), "leave alone")
        self.assertEqual((old_tree / "sentinel").read_text(), "old version")
        self.assertEqual((main / "auth.json").read_text(), '{"synthetic":true}\n')
        self.assertEqual((main / "models-store.json").read_text(), '{"synthetic":true}\n')
        self.assertEqual((main / "sessions/sentinel").read_text(), "session")
        self.assertEqual((pi1_dir / "APPEND_SYSTEM.md").read_text(), "main APPEND_SYSTEM.md")
        self.assertEqual((pi1_dir / "SYSTEM.md").read_text(), "main SYSTEM.md")
        for resource in ("prompts", "skills", "themes", "resource-groups"):
            self.assertTrue((pi1_dir / resource).exists(), f"pi1 did not inherit {resource}")
        self.assertEqual((pi1_dir / "extensions/sentinel").resolve(),
                         (main / "extensions/sentinel").resolve())

    def test_install_is_repeatable_and_version_switch_preserves_trees(self):
        self.install(VERSIONS[0])
        self.install(VERSIONS[0])
        self.assertEqual(self.npm_log.read_text().splitlines(), [VERSIONS[0]])
        first_tree = self.home / ".local/share/pi-managed/pi-cli" / VERSIONS[0]
        sentinel = first_tree / "version-sentinel"
        sentinel.write_text("preserve first version")
        self.install(VERSIONS[1])
        self.assertEqual((first_tree / "version-sentinel").read_text(), "preserve first version")
        self.assertEqual(self.npm_log.read_text().splitlines(), list(VERSIONS))
        self.assertEqual(self.invoke(self.home / ".local/bin/pi", "--version"), VERSIONS[1])

    def _chezmoi_fixture(self):
        source = self.root / "source"
        for relative in (
            "private_dot_pi/private_agent/modify_settings.json",
            "private_dot_pi/instances/private_pi0/modify_settings.json",
            "private_dot_pi/instances/private_pi1/modify_settings.json",
        ):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        runtime = self.root / "runtime"
        (runtime / "pi-subagents/src").mkdir(parents=True)
        (runtime / "pi-vcc").mkdir()
        (runtime / "pi-subagents/src/index.ts").write_text("// synthetic\n")
        (runtime / "pi-vcc/index.ts").write_text("// synthetic\n")
        (runtime / "manifest.json").write_text(json.dumps({
            "cli": {"package": PACKAGE, "version": "1.4.0"},
            "extensions": {"pi-subagents": {"version": "2.9.1"}, "pi-vcc": {"version": "0.7.2"}},
        }))
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        log = self.root / "chezmoi.log"
        fake = fake_bin / "chezmoi"
        fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                        'with open(os.environ["CHEZMOI_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        fake.chmod(0o755)
        return source, runtime, fake_bin, log

    def run_chezmoi_pi(self, command, source, runtime, fake_bin, log):
        env = {**os.environ, "HOME": str(self.home), "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
               "CHEZMOI_SOURCE_DIR": str(source), "PI_MANAGED_RUNTIME_DIR": str(runtime),
               "CHEZMOI_LOG": str(log)}
        return subprocess.run(["bash", "-euo", "pipefail", str(CHEZMOI_PI), command], env=env,
                              text=True, capture_output=True)

    def test_chezmoi_diff_apply_order_and_closed_source_guard(self):
        source, runtime, fake_bin, log = self._chezmoi_fixture()
        diff = self.run_chezmoi_pi("diff", source, runtime, fake_bin, log)
        self.assertEqual(diff.returncode, 0, diff.stderr)
        diff_calls = [json.loads(line) for line in log.read_text().splitlines()]
        expected_targets = [str(self.home / ".pi/agent"),
                            str(self.home / ".pi/instances/pi0"),
                            str(self.home / ".pi/instances/pi1")]
        self.assertEqual(diff_calls, [["--source", str(source), "diff", "--recursive", *expected_targets]])
        log.write_text("")
        apply = self.run_chezmoi_pi("apply", source, runtime, fake_bin, log)
        self.assertEqual(apply.returncode, 0, apply.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(calls, [
            ["--source", str(source), "apply", expected_targets[0]],
            ["--source", str(source), "apply", *expected_targets[1:]],
        ])
        self.assertTrue(all(Path(target).is_dir() for target in expected_targets))

        log.write_text("")
        (source / "private_dot_pi/instances/private_pi0/modify_settings.json").unlink()
        for command in ("diff", "apply"):
            rejected = self.run_chezmoi_pi(command, source, runtime, fake_bin, log)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(log.read_text(), "", "outdated source must fail before invoking chezmoi")

        (source / "private_dot_pi/instances/private_pi0/modify_settings.json").write_text("{}\n")
        manifest_path = runtime / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["extensions"]["pi-vcc"]["version"] = "0.0.0"
        manifest_path.write_text(json.dumps(manifest))
        rejected = self.run_chezmoi_pi("apply", source, runtime, fake_bin, log)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(log.read_text(), "", "mismatched runtime must fail before invoking chezmoi")

    def test_optional_real_chezmoi_settings_and_coder(self):
        reviewed_source = os.environ.get("PI_TEST_CHEZMOI_SOURCE")
        if not reviewed_source:
            self.skipTest("set PI_TEST_CHEZMOI_SOURCE to enable the reviewed-source integration test")
        chezmoi = shutil.which("chezmoi")
        if not chezmoi:
            self.skipTest("chezmoi is required for the optional integration test")
        reviewed_source = Path(reviewed_source)
        allowed = {
            "private_dot_pi/private_agent/modify_settings.json": "private_dot_pi/private_agent/modify_settings.json",
            "private_dot_pi/instances/private_pi0/modify_settings.json": "private_dot_pi/instances/private_pi0/modify_settings.json",
            "private_dot_pi/instances/private_pi1/modify_settings.json": "private_dot_pi/instances/private_pi1/modify_settings.json",
            "private_dot_pi/private_agent/agents/coder.md": "private_dot_pi/private_agent/agents/coder.md",
        }
        source = self.root / "paired-source"
        for relative in allowed:
            origin = reviewed_source / relative
            self.assertTrue(origin.is_file(), f"reviewed source is missing {relative}")
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
            destination.chmod(origin.stat().st_mode & 0o777)

        runtime = self.root / "paired-runtime"
        (runtime / "pi-subagents/src").mkdir(parents=True)
        (runtime / "pi-vcc").mkdir()
        (runtime / "pi-subagents/src/index.ts").write_text("// synthetic runtime\\n")
        (runtime / "pi-vcc/index.ts").write_text("// synthetic runtime\\n")
        (runtime / "manifest.json").write_text(json.dumps({
            "cli": {"package": PACKAGE, "version": "1.4.0"},
            "extensions": {"pi-subagents": {"version": "2.9.1"}, "pi-vcc": {"version": "0.7.2"}},
        }))

        main = self.home / ".pi/agent"
        pi0 = self.home / ".pi/instances/pi0"
        pi1 = self.home / ".pi/instances/pi1"
        settings_fixtures = {
            main: {"defaultProvider": "openai-codex", "defaultModel": "user-main",
                   "defaultThinkingLevel": "high", "theme": "solarized", "packages": ["npm:user-main"]},
            pi0: {"defaultProvider": "openai-codex", "defaultModel": "user-pi0",
                  "defaultThinkingLevel": "high", "customPi0": True, "packages": ["npm:user-pi0"]},
            pi1: {"defaultProvider": "openai-codex", "defaultModel": "user-pi1",
                  "defaultThinkingLevel": "high", "customPi1": True, "packages": ["npm:user-pi1"]},
        }
        for directory, settings in settings_fixtures.items():
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "settings.json").write_text(json.dumps(settings) + "\n")
        sentinels = {
            main / "auth.json": '{"synthetic":"auth"}\n',
            main / "models-store.json": '{"synthetic":"models"}\n',
            main / "sessions/sentinel": "synthetic session\n",
            pi0 / "auth.json": '{"synthetic":"pi0"}\n',
            pi1 / "sessions/sentinel": "synthetic pi1 session\n",
        }
        for path, contents in sentinels.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)

        env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "CHEZMOI_SOURCE_DIR": str(source),
            "PI_MANAGED_RUNTIME_DIR": str(runtime),
            "PI_SETTINGS_PLATFORM_OVERRIDE": "linux",
        }
        for _ in range(2):
            result = subprocess.run(["bash", "-euo", "pipefail", str(CHEZMOI_PI), "apply"],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        expected = {
            main: {"theme": "solarized", "defaultProvider": "openai-codex",
                   "defaultModel": "gpt-6-luna", "defaultThinkingLevel": "medium",
                   "defaultProjectTrust": "ask", "enableInstallTelemetry": False,
                   "enableAnalytics": False,
                   "prompts": ["resource-groups/base/prompts", "resource-groups/pi/prompts"],
                   "idleNotify": {"enabled": False},
                   "packages": ["npm:user-main", "npm:@latentminds/pi-quotas",
                                "npm:@heyhuynhgiabuu/pi-pretty", "git:github.com/luketych/pi-idle-notify"]},
            pi0: {**settings_fixtures[pi0], "defaultModel": "gpt-6-luna", "defaultThinkingLevel": "medium"},
            pi1: {**settings_fixtures[pi1], "defaultModel": "gpt-6-luna", "defaultThinkingLevel": "medium"},
        }
        snapshots = {}
        for directory, values in expected.items():
            settings = json.loads((directory / "settings.json").read_text())
            self.assertEqual(settings, values)
            snapshots[directory] = settings
        coder = (main / "agents/coder.md").read_text()
        self.assertIn("model: openai-codex/gpt-6-luna", coder)
        self.assertIn("thinking: high", coder)
        for path, contents in sentinels.items():
            self.assertEqual(path.read_text(), contents)

        result = subprocess.run(["bash", "-euo", "pipefail", str(CHEZMOI_PI), "apply"],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for directory, values in snapshots.items():
            self.assertEqual(json.loads((directory / "settings.json").read_text()), values)
        for path, contents in sentinels.items():
            self.assertEqual(path.read_text(), contents)


if __name__ == "__main__":
    unittest.main()
