"""Isolated tests for the DHCP resilience harness; no real SSH or network actions."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.network_resilience.harness import (
    HarnessError, PROBE_SCRIPT, RemoteCommandError, Runner, SYSTEM_JOB_PATH, Suite, TransportError, health, main, spare_port_errors, ssh_args, valid_ip,
    activation_command, valid_python_path, valid_system_path,
)


UUID = "0e4ea8c2-74ea-34a9-80fe-a80251d38aa2"


def healthy(ip="192.168.4.66", method="auto", boot="boot-a"):
    return {
        "addresses": [{"interface": "enp2s2", "address": ip + "/22"}],
        "macs": {"enp2s2": "00:e0:4c:5b:96:3e", "enp3s0": "00:e0:4c:5b:96:3f"},
        "default_routes": [{"device": "enp2s2", "gateway": "192.168.4.1"}], "dns_values": ["192.168.4.1", "1.1.1.1"],
        "gateway": "192.168.4.1", "dns_present": True, "name_resolution": True, "connection_uuid": UUID,
        "ipv4_method": method, "profiles": [{"id": "zimaboard-lan", "uuid": UUID,
            "method": method, "mac": ""}], "boot_id": boot,
        "system_path": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-current",
        "python_path": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-python3-3.14.7/bin/python3.14",
        "services": {x: "active" for x in ("NetworkManager", "sshd", "caddy")},
        "peertube_http": 302,
    }


class FakeRunner:
    host = "192.168.4.75"
    def __init__(self, probes=None, states=None):
        self.probes = list(probes or [healthy()])
        self.states = list(states or ["ActiveState=active\nSubState=exited\nResult=success"])
        self.commands = []
        self.stopped_timers = set()
        self.stopped_services = set()
    def probe(self):
        return self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
    def remote(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if command.startswith("sudo systemctl stop"):
            self.stopped_timers.update(part for part in command.split() if part.endswith(".timer"))
            self.stopped_services.update(part for part in command.split() if part.endswith(".service"))
            return ""
        if command.startswith("systemctl is-active"):
            unit = command.rsplit(" ", 1)[-1]
            return ("inactive" if unit in self.stopped_timers else "active") + "\n"
        if command.startswith("systemctl show"):
            if ".timer " in command or command.endswith("-p ActiveState --value"):
                unit = command.split()[2]
                stopped = self.stopped_timers | self.stopped_services
                return ("inactive" if unit in stopped else "active") + "\n"
            if "ExecMainStartTimestampMonotonic" in command:
                return "Result=success\nExecMainStartTimestampMonotonic=0\nSubState=dead\nActiveState=inactive\n"
            return (self.states.pop(0) if len(self.states) > 1 else self.states[0]) + "\n"
        return ""


class NetworkResilienceTests(unittest.TestCase):
    def test_cli_default_smoke_is_read_only_and_dry_run_never_ssh_or_sudo(self):
        with patch("scripts.network_resilience.harness.Runner") as runner:
            self.assertEqual(main(["--host", "192.168.4.75", "--dry-run"]), 0)
            runner.return_value.remote.assert_not_called()

    def test_default_smoke_runs_only_read_only_probe(self):
        with patch("scripts.network_resilience.harness.Runner") as factory:
            runner = factory.return_value
            runner.host = "192.168.4.75"
            runner.probe.return_value = healthy()
            self.assertEqual(main(["--host", "192.168.4.75"]), 0)
            runner.probe.assert_called_once_with()
            runner.remote.assert_not_called()

    def test_explicit_baseline_only_cases_in_dhcp_and_static_never_schedule(self):
        for suite_name, probe in (("dhcp", healthy()), ("static", healthy(method="manual"))):
            with self.subTest(suite=suite_name), patch("scripts.network_resilience.harness.Runner") as factory:
                runner = factory.return_value
                runner.host = "192.168.4.75"
                runner.probe.return_value = probe
                self.assertEqual(main(["--host", "192.168.4.75", "--suite", suite_name, "--cases", "baseline"]), 0)
                runner.probe.assert_called_once_with()
                runner.remote.assert_not_called()

    def test_cli_rejects_each_missing_disruption_ack(self):
        required = ("--allow-disruptive", "--reservation-confirmed", "--console-available")
        for omitted in required:
            args = ["--host", "192.168.4.75", "--suite", "dhcp", "--cases", "reconnect"]
            args += [flag for flag in required if flag != omitted]
            with self.subTest(omitted=omitted), self.assertRaises(SystemExit):
                main(args)

    def test_reboot_has_separate_ack(self):
        with self.assertRaises(SystemExit):
            main(["--host", "192.168.4.75", "--suite", "dhcp", "--cases", "reboot", "--allow-disruptive",
                  "--reservation-confirmed", "--console-available"])

    def test_static_manual_profile_is_rejected(self):
        self.assertIn("not DHCP", ", ".join(health(healthy(method="manual"), "192.168.4.66")))

    def test_alias_and_strict_host_key_flags_are_common_for_address_changes(self):
        for host in ("192.168.4.75", "192.168.4.66"):
            args = ssh_args(host, "192.168.4.75", "luke")
            self.assertIn("StrictHostKeyChecking=yes", args)
            self.assertIn("CheckHostIP=no", args)
            self.assertIn("HostKeyAlias=192.168.4.75", args)
            self.assertIn("BatchMode=yes", args)

    def test_transition_only_permits_configured_original_address(self):
        old = healthy("192.168.4.75")
        self.assertTrue(health(old, "192.168.4.66"))
        self.assertFalse(health(old, "192.168.4.66", transition=True, original_ip="192.168.4.75"))
        self.assertTrue(health(old, "192.168.4.66", transition=True, original_ip="192.168.4.76"))

    def test_job_must_finish_before_post_action_probe(self):
        runner = FakeRunner(states=["ActiveState=active\nSubState=running\nResult=success",
                                    "ActiveState=active\nSubState=exited\nResult=success"])
        args = SimpleNamespace(expected_ip="192.168.4.66", suite="dhcp", cycles=1,
                               cases=["reconnect"], allow_address_transition=False)
        clock = Mock(side_effect=[0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        suite = Suite(runner, args, clock=clock, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("reconnect", "192.168.4.66")
        show = [c for c, _ in runner.commands if c.startswith("systemctl show")]
        self.assertGreaterEqual(len(show), 2)
        self.assertTrue(any(c.startswith("sudo systemctl stop") for c, _ in runner.commands))

    def test_repeated_reboots_compare_each_boot_to_the_previous_boot(self):
        runner = FakeRunner(probes=[healthy(boot="boot-a"), healthy(boot="boot-b"), healthy(boot="boot-c")])
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="dhcp", cycles=2,
                               cases=["reboot"], allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("reboot", "192.168.4.66")
        self.assertEqual(suite.boot_id, "boot-b")
        suite.disruptive("reboot", "192.168.4.66")
        self.assertEqual(suite.boot_id, "boot-c")

    def test_static_suite_allows_reboot_only_as_an_explicit_guarded_case(self):
        with self.assertRaises(SystemExit):
            main(["--host", "192.168.4.75", "--suite", "static", "--cases", "reboot",
                  "--allow-reboot"])
        with patch("scripts.network_resilience.harness.Runner") as runner:
            self.assertEqual(main(["--host", "192.168.4.75", "--suite", "static", "--cases", "reboot",
                                  "--allow-reboot", "--allow-disruptive", "--reservation-confirmed",
                                  "--console-available", "--dry-run"]), 0)
            runner.assert_not_called()

    def test_static_reboot_uses_manual_profile_and_no_rollback_claim(self):
        runner = FakeRunner(probes=[healthy(method="manual", boot="boot-a"),
                                    healthy(method="manual", boot="boot-b")])
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="static", cycles=1,
                               cases=["reboot"], allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66", method="manual")
        suite.disruptive("reboot", "192.168.4.66")
        self.assertEqual(suite.report["cases"][-1]["recovery"], "no_timer_survives_reboot")
        self.assertEqual(suite.boot_id, "boot-b")

    def test_reboot_requires_changed_boot_id(self):
        runner = FakeRunner(probes=[healthy(boot="boot-a"), healthy(boot="boot-a"), healthy(boot="boot-b")])
        args = SimpleNamespace(expected_ip="192.168.4.66", suite="dhcp", cycles=1, cases=["reboot"],
                               allow_address_transition=False)
        ticks = iter(range(100))
        suite = Suite(runner, args, clock=lambda: next(ticks), sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("reboot", "192.168.4.66")
        self.assertTrue(any("systemd-run" in c for c, _ in runner.commands))
        self.assertTrue(suite.report["cases"][-1]["reboot_verified"])
        self.assertNotIn("boot-a", json.dumps(suite.report))

    def test_scheduler_commands_are_detached_delayed_and_bounded(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(host=runner.host, suite="dhcp"), clock=lambda: 0)
        suite._schedule(suite.unit("action"), "true", runtime=180)
        action = runner.commands[-1][0]
        self.assertIn("--no-block", action)
        self.assertIn("--on-active=5s", action)
        self.assertIn("TimeoutStartSec=180s", action)
        suite._schedule(suite.unit("watchdog"), "true", timer=True, delay=600, runtime=180)
        watchdog = runner.commands[-1][0]
        self.assertIn("--on-active=600s", watchdog)
        self.assertIn("TimeoutStartSec=180s", watchdog)
        self.assertIn("--setenv=PATH=" + SYSTEM_JOB_PATH, watchdog)
        self.assertIn("--setenv=PATH=" + SYSTEM_JOB_PATH, action)
        self.assertNotIn("RuntimeMaxSec", watchdog)
        suite._schedule(suite.unit("static-watchdog"), "true", timer=True, delay=900, runtime=180)
        self.assertIn("--on-active=900s", runner.commands[-1][0])

    def test_unique_job_names_and_watchdog_cancel_never_stops_service(self):
        runner = FakeRunner()
        suite_a = Suite(runner, SimpleNamespace(host=runner.host, suite="dhcp"), clock=lambda: 0)
        suite_b = Suite(runner, SimpleNamespace(host=runner.host, suite="dhcp"), clock=lambda: 0)
        self.assertNotEqual(suite_a.unit("recovery-01"), suite_b.unit("recovery-01"))

        class Started(FakeRunner):
            def remote(self, command, **kwargs):
                self.commands.append((command, kwargs))
                if command.startswith("systemctl show"):
                    return "Result=success\nSubState=running\nExecMainStartTimestampMonotonic=1234\nActiveState=active\n"
                return ""
        started = Started()
        suite = Suite(started, SimpleNamespace(host=started.host, suite="dhcp"), clock=lambda: 0)
        timer = suite.unit("watchdog")
        with self.assertRaisesRegex(HarnessError, "has started"):
            suite._cancel_watchdog(timer)
        self.assertFalse(any("systemctl stop" in c for c, _ in started.commands))

    def test_watchdog_start_race_stops_timer_only_and_reports_active_recovery(self):
        class StartsDuringStop(FakeRunner):
            def __init__(self):
                super().__init__()
                self.state_reads = 0
            def remote(self, command, **kwargs):
                self.commands.append((command, kwargs))
                if command.startswith("systemctl show"):
                    if ".timer " in command:
                        return "inactive\n"
                    self.state_reads += 1
                    state = "Result=success\nExecMainStartTimestampMonotonic=0\nSubState=dead\nActiveState=inactive\n" if self.state_reads == 1 else "Result=success\nSubState=running\nExecMainStartTimestampMonotonic=1234\nActiveState=active\n"
                    return state
                if command.startswith("systemctl is-active"): return "inactive\n"
                return ""
        runner = StartsDuringStop()
        suite = Suite(runner, SimpleNamespace(host=runner.host, suite="dhcp"), clock=lambda: 0)
        unit = suite.unit("watchdog")
        with self.assertRaisesRegex(HarnessError, "started during cancellation"):
            suite._cancel_watchdog(unit)
        stops = [c for c, _ in runner.commands if c.startswith("sudo systemctl stop")]
        self.assertEqual(len(stops), 1)
        self.assertIn(unit + ".timer", stops[0])
        self.assertNotIn(unit + ".service", stops[0])
        self.assertEqual(suite.recovery_state, "active_not_cancelled")

    def test_recovery_timer_precedes_action_and_is_cancelled_after_validation(self):
        runner = FakeRunner()
        args = SimpleNamespace(expected_ip="192.168.4.66", suite="dhcp", cycles=1, cases=["link-bounce"],
                               allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("link-bounce", "192.168.4.66")
        cmds = [c for c, _ in runner.commands]
        recovery = next(i for i, c in enumerate(cmds) if "--unit=" in c and "-recovery-01" in c)
        action = next(i for i, c in enumerate(cmds) if "--unit=" in c and "-link-bounce-01" in c)
        cancel = next(i for i, c in enumerate(cmds) if c.startswith("sudo systemctl stop") and ".timer" in c and "recovery-" in c)
        self.assertLess(recovery, action)
        self.assertLess(action, cancel)

    def test_temporary_ssh_outage_address_transition_moves_later_root_calls_to_verified_host(self):
        old_probe = healthy("192.168.4.75")
        new_probe = healthy("192.168.4.66")
        calls = []
        outage_used = False
        network_lost = False
        stopped = set()
        def fake_ssh(argv, **kwargs):
            nonlocal outage_used, network_lost
            destination, command = next(x for x in argv if x.startswith("luke@")), argv[-1]
            host = destination.split("@", 1)[1]
            calls.append((host, command))
            if command.startswith("python3 -c"):
                if host == "192.168.4.75" and network_lost:
                    return subprocess.CompletedProcess(argv, 255, "", "connection reset")
                return subprocess.CompletedProcess(argv, 0, json.dumps(old_probe if host == "192.168.4.75" else new_probe), "")
            if command.startswith("systemctl show"):
                if ".timer " in command or command.endswith("-p ActiveState --value"):
                    target_unit = command.split()[2]
                    return subprocess.CompletedProcess(argv, 0, "inactive\n" if target_unit in stopped else "active\n", "")
                if "ExecMainStartTimestampMonotonic" in command:
                    return subprocess.CompletedProcess(argv, 0, "Result=success\nExecMainStartTimestampMonotonic=0\nSubState=dead\nActiveState=inactive\n", "")
                if host == "192.168.4.75" and not outage_used:
                    outage_used = True
                    return subprocess.CompletedProcess(argv, 255, "", "connection reset")
                if "ExecMainStartTimestampMonotonic" in command:
                    return subprocess.CompletedProcess(argv, 0, "SubState=exited\nActiveState=active\nResult=success\nExecMainStartTimestampMonotonic=0\n", "")
                return subprocess.CompletedProcess(argv, 0, "active\nexited\nsuccess\n", "")
            if command.startswith("systemctl is-active"):
                timer = command.split()[-1]
                return subprocess.CompletedProcess(argv, 0, "inactive\n" if timer in stopped else "active\n", "")
            if command.startswith("sudo systemctl stop"):
                stopped.update(x for x in command.split() if x.endswith((".timer", ".service")))
            if command.startswith("sudo systemd-run") and "-reconnect-01" in command:
                network_lost = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        runner = Runner("192.168.4.75", "192.168.4.75", "luke", call=fake_ssh,
                        probe_hosts=("192.168.4.75", "192.168.4.66"))
        args = SimpleNamespace(host="192.168.4.75", expected_ip="192.168.4.66", suite="dhcp", cycles=1,
                               cases=["reconnect"], allow_address_transition=True)
        ticks = iter(range(1000))
        suite = Suite(runner, args, clock=lambda: next(ticks), sleep=lambda _: None)
        with patch("scripts.network_resilience.harness.sys.stdin.isatty", return_value=True):
            suite.baseline("192.168.4.66", transition=True)
            self.assertEqual(suite.report["cases"][0]["status"], "transition_pending")
            self.assertEqual(suite.report["cases"][0]["observed_ip"], "192.168.4.75")
            suite.disruptive("reconnect", "192.168.4.66")
        self.assertTrue(outage_used)
        self.assertEqual(runner.host, "192.168.4.66")
        root_schedule = [(h, c) for h, c in calls if c.startswith("sudo systemd-run")]
        self.assertEqual([h for h, _ in root_schedule], ["192.168.4.75", "192.168.4.75"])
        self.assertTrue(any(h == "192.168.4.66" and "sudo systemctl stop" in c for h, c in calls))

    def test_recovery_arm_failure_prevents_action(self):
        class FailedArm(FakeRunner):
            def remote(self, command, **kwargs):
                self.commands.append((command, kwargs))
                if command.startswith("systemctl is-active"): return "inactive"
                return ""
        runner = FailedArm()
        args = SimpleNamespace(expected_ip="192.168.4.66", suite="dhcp", cycles=1,
                               cases=["reconnect"], allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        with self.assertRaisesRegex(HarnessError, "failed to arm"):
            suite.disruptive("reconnect", "192.168.4.66")
        self.assertFalse(any("--unit=" in c and "reconnect-" in c for c, _ in runner.commands))

    def test_cleanup_failure_is_reported(self):
        class CleanupFails(FakeRunner):
            def remote(self, command, **kwargs):
                self.commands.append((command, kwargs))
                if command.startswith("sudo systemctl stop") and "recovery-" in command and ".timer" in command: raise HarnessError("stop failed")
                if command.startswith("systemctl is-active"):
                    if command.endswith(".timer") and command.split()[-1] in self.stopped_timers: return "inactive"
                    return "active"
                if command.startswith("systemctl show") or command.startswith("sudo systemctl show"):
                    if "ExecMainStartTimestampMonotonic" in command: return "Result=success\nExecMainStartTimestampMonotonic=0\nSubState=dead\nActiveState=inactive\n"
                    return "ActiveState=active\nSubState=exited\nResult=success"
                return ""
        runner = CleanupFails()
        suite = Suite(runner, SimpleNamespace(expected_ip="192.168.4.66", suite="dhcp", cycles=1,
                     cases=[], allow_address_transition=False), clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        with self.assertRaisesRegex(HarnessError, "cleanup was not confirmed"):
            suite.disruptive("reconnect", "192.168.4.66")

    def test_remote_probe_parses_indexed_dns_blank_mac_and_selects_lan_active_profile(self):
        def result(args, stdout="", returncode=0):
            return subprocess.CompletedProcess(args, returncode, stdout, "")
        lan_uuid = UUID
        recovery_uuid = "d63ad997-9ab2-55d0-bc18-015b25571456"
        def fake_run(args, **kwargs):
            args = list(args)
            joined = " ".join(args)
            if args[:4] == ["ip", "-o", "-4", "addr"]:
                return result(args, "2: enp2s2 inet 192.168.4.75/22 brd 192.168.7.255 scope global enp2s2\n")
            if args[:2] == ["cat", "/sys/class/net/enp2s2/address"]:
                return result(args, "00:e0:4c:5b:96:3e\n")
            if args[:2] == ["cat", "/sys/class/net/docker0/address"]:
                return result(args, "02:42:ac:11:00:01\n")
            if args[:2] == ["cat", "/sys/class/net/lo/address"]:
                return result(args, "00:00:00:00:00:00\n")
            if "route" in args:
                return result(args, "default via 172.17.0.1 dev docker0\ndefault via 192.168.4.1 dev enp2s2\n")
            if any("IP4.DNS" in arg for arg in args):
                self.assertIn("enp2s2", args)
                return result(args, "GENERAL.DEVICE:enp2s2\nIP4.DNS[1]:192.168.4.1\nIP4.DNS[2]:1.1.1.1\n")
            if "ahostsv4" in args:
                return result(args, "1.1.1.1 STREAM example\n")
            if "--active" in args:
                return result(args, "docker-uuid:bridge:docker0\n" + lan_uuid + ":802-3-ethernet:enp2s2\n")
            if "ipv4.method" in args:
                return result(args, "auto\n")
            if len(args) > 2 and args[2] == "connection.uuid" and "id" in args:
                return result(args, recovery_uuid + "\n")
            if len(args) > 2 and args[2] in ("connection.id", "connection.uuid", "ipv4.method", "802-3-ethernet.mac-address"):
                profile = args[-1]
                label = "current" if profile == lan_uuid else "zimaboard-lan-dhcp"
                mac = "" if profile == lan_uuid else "00:e0:4c:5b:96:3e"
                key = args[2]
                value = {"connection.id": label, "connection.uuid": profile,
                         "ipv4.method": "auto", "802-3-ethernet.mac-address": mac}[key]
                return result(args, value + "\n")
            if "systemctl" in args and "is-active" in args:
                return result(args, "active\n")
            if "boot_id" in joined: return result(args, "synthetic-boot\n")
            if "current-system" in joined: return result(args, "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-current\n")
            return result(args)
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
        output = []
        with (
            patch("scripts.network_resilience.harness.subprocess.run", side_effect=fake_run),
            patch("os.listdir", return_value=["enp2s2", "docker0", "lo"]),
            patch("urllib.request.urlopen", return_value=Response()),
            patch("builtins.print", side_effect=lambda value: output.append(value)),
        ):
            with patch("sys.argv", ["-c", "zimaboard-lan-dhcp"]):
                exec(PROBE_SCRIPT, {"__name__": "probe_fixture"})
        parsed = json.loads(output[0])
        self.assertEqual(parsed["connection_uuid"], lan_uuid)
        self.assertEqual(parsed["dns_values"], ["192.168.4.1", "1.1.1.1"])
        self.assertEqual(parsed["gateway"], "192.168.4.1")
        active = next(x for x in parsed["profiles"] if x["uuid"] == lan_uuid)
        self.assertEqual(active["mac"], "")
        self.assertEqual(health(parsed, "192.168.4.75"), [])

    def test_health_diagnostics_do_not_include_boot_identifier(self):
        errors = health(healthy(boot="private-test-id"), "192.168.4.66")
        self.assertEqual(errors, [])
        self.assertNotIn("boot_id", json.dumps({"errors": errors}))

    def test_static_method_guard_and_spare_port_conflicts(self):
        manual = healthy(method="manual")
        self.assertEqual(health(manual, "192.168.4.66", method="manual"), [])
        self.assertTrue(any("not DHCP" in x for x in health(manual, "192.168.4.66", method="auto")))
        self.assertEqual(spare_port_errors(manual), [])
        manual["addresses"].append({"interface": "enp3s0", "address": "192.168.4.99/22"})
        manual["default_routes"].append({"device": "enp3s0", "gateway": "192.168.4.1"})
        self.assertEqual(len(spare_port_errors(manual)), 2)

    def static_probe(self, method="manual", address="192.168.4.66", system=None):
        probe = healthy(address, method=method)
        static_uuid = UUID
        dhcp_uuid = "d63ad997-9ab2-55d0-bc18-015b25571456"
        probe["connection_uuid"] = static_uuid if method == "manual" else dhcp_uuid
        probe["ipv4_method"] = method
        probe["profiles"] = [
            {"id": "zimaboard-lan-static", "uuid": static_uuid, "method": "manual", "mac": "00:e0:4c:5b:96:3e"},
            {"id": "zimaboard-lan-dhcp", "uuid": dhcp_uuid, "method": "auto", "mac": "00:e0:4c:5b:96:3e"},
        ]
        if system: probe["system_path"] = system
        return probe

    def test_dhcp_roundtrip_checks_both_modes_restores_static_and_cancels_watchdog_last(self):
        probes = [self.static_probe(), self.static_probe(), self.static_probe("auto"), self.static_probe("manual")]
        runner = FakeRunner(probes=probes)
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="static",
                               cycles=1, cases=[], allow_address_transition=False,
                               dhcp_profile="zimaboard-lan-dhcp")
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66", method="manual")
        suite.static_roundtrip("192.168.4.66")
        self.assertEqual([x["status"] for x in suite.report["cases"][-3:]], ["verified"] * 3)
        commands = [x[0] for x in runner.commands]
        arm = next(i for i, c in enumerate(commands) if "-static-rollback" in c and "systemd-run" in c)
        dhcp = next(i for i, c in enumerate(commands) if "-roundtrip-dhcp" in c and "systemd-run" in c)
        restore = next(i for i, c in enumerate(commands) if "-roundtrip-static" in c and "systemd-run" in c)
        cancel = next(i for i, c in enumerate(commands) if "systemctl stop" in c and "-static-rollback.timer" in c)
        self.assertLess(arm, dhcp)
        self.assertLess(dhcp, restore)
        self.assertLess(restore, cancel)
        self.assertIn(UUID, commands[restore])

    def test_static_restart_recovery_uses_captured_static_uuid(self):
        runner = FakeRunner(probes=[self.static_probe()])
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="static", cycles=1,
                               cases=[], allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66", method="manual")
        suite.disruptive("restart-networkmanager", "192.168.4.66")
        recovery = next(c for c, _ in runner.commands if "systemd-run" in c and "recovery-01" in c)
        self.assertIn(UUID, recovery)
        self.assertNotIn("d63ad997-9ab2-55d0-bc18-015b25571456", recovery)

    def test_failed_dhcp_roundtrip_keeps_watchdog_and_reports_failed_test(self):
        runner = FakeRunner(probes=[self.static_probe(), self.static_probe()])
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="static", cycles=1,
                               cases=[], allow_address_transition=False, dhcp_profile="zimaboard-lan-dhcp")
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66", method="manual")
        suite._wait_job = Mock()
        suite._wait = Mock(side_effect=HarnessError("DHCP returned wrong address"))
        with self.assertRaisesRegex(HarnessError, "wrong address"):
            suite.static_roundtrip("192.168.4.66")
        self.assertEqual(suite.report["cases"][-1]["status"], "failed")
        self.assertEqual(suite.report["cases"][-1]["recovery"], "watchdog_pending")
        self.assertFalse(any("systemctl stop" in c for c, _ in runner.commands))

    def test_recovery_profile_must_be_named_auto_and_match_lan_mac(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(host=runner.host, suite="static"), clock=lambda: 0)
        with self.assertRaisesRegex(HarnessError, "method or LAN MAC"):
            suite._profile_uuid({"profiles": [{"id": "zimaboard-lan-dhcp", "uuid": UUID,
                                  "method": "manual", "mac": "00:e0:4c:5b:96:3e"}]},
                                "zimaboard-lan-dhcp", "auto")

    def test_python_store_path_is_strictly_validated(self):
        self.assertTrue(valid_python_path(healthy()["python_path"]))
        self.assertFalse(valid_python_path("/Users/luke/.nix-profile/bin/python3"))

    def test_generation_path_validation_and_opt_in_guards(self):
        current = healthy()["system_path"]
        previous = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-previous"
        self.assertTrue(valid_system_path(current))
        self.assertFalse(valid_system_path("/tmp/nixos-system-previous"))
        with self.assertRaises(SystemExit):
            main(["--host", "192.168.4.75", "--suite", "static", "--cases", "generation-roundtrip",
                  "--dry-run"])
        with self.assertRaises(SystemExit):
            main(["--host", "192.168.4.75", "--suite", "static", "--cases", "generation-roundtrip",
                  "--allow-system-switch", "--dry-run"])
        with patch("scripts.network_resilience.harness.Runner") as runner:
            self.assertEqual(main(["--host", "192.168.4.75", "--suite", "dhcp", "--dry-run"]), 0)
            runner.assert_not_called()

    def test_generation_roundtrip_proves_identity_and_arms_rollback_first(self):
        current = healthy()["system_path"]
        previous = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-previous"
        probes = [self.static_probe(system=current), self.static_probe(system=current),
                  self.static_probe("auto", system=previous),
                  self.static_probe("manual", system=current)]
        runner = FakeRunner(probes=probes)
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="static", cycles=1,
                               cases=["generation-roundtrip"], allow_address_transition=False,
                               dhcp_profile="zimaboard-lan-dhcp", previous_system=previous)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66", method="manual")
        suite.generation_roundtrip("192.168.4.66")
        cmds = [x[0] for x in runner.commands]
        arm = next(i for i, c in enumerate(cmds) if "generation-rollback" in c and "systemd-run" in c)
        apply = next(i for i, c in enumerate(cmds) if "generation-previous" in c and "systemd-run" in c)
        restore = next(i for i, c in enumerate(cmds) if "generation-restore" in c and "systemd-run" in c)
        self.assertLess(arm, apply)
        self.assertLess(apply, restore)
        self.assertTrue(suite.report["cases"][-2]["generation_restored"])
        self.assertNotIn(current, json.dumps(suite.report))
        self.assertNotIn(previous, json.dumps(suite.report))
        helper_command = next(c for c in cmds if "python3.14" in c and "realpath" in c)
        self.assertIn("/run/current-system", helper_command)
        self.assertIn("--on-active=1800s", helper_command)
        self.assertIn("TimeoutStartSec=420s", helper_command)
        self.assertIn("--setenv=PATH=" + SYSTEM_JOB_PATH, helper_command)
        self.assertNotIn("RuntimeMaxSec", helper_command)
        self.assertIn("/nix/store/0123456789abcdfghijklmnpqrsvwxyz-python3-3.14.7/bin/python3.14", helper_command)

    def test_runner_bounds_and_reports_oserror_without_exposing_details(self):
        call = Mock(side_effect=OSError("synthetic private path"))
        runner = Runner("192.168.4.75", "192.168.4.75", "luke", call=call,
                        probe_hosts=("192.168.4.66",))
        with self.assertRaisesRegex(HarnessError, "SSH transport error") as caught:
            runner.remote("read-only", fallback=True)
        self.assertNotIn("synthetic private path", str(caught.exception))
        self.assertEqual(call.call_count, 2)

    def test_interruption_reports_case_and_leaves_recovery_untouched(self):
        printed = []
        with (
            patch("scripts.network_resilience.harness.Runner") as factory,
            patch("builtins.print", side_effect=lambda value: printed.append(value)),
        ):
            factory.return_value.host = "192.168.4.75"
            factory.return_value.probe.side_effect = KeyboardInterrupt
            result = main(["--host", "192.168.4.75"])
        report = json.loads(printed[-1])
        self.assertEqual(result, 130)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["failure"], {"case": "baseline", "recovery": "not_armed"})

    def test_sudo_action_requires_visible_tty_before_subprocess(self):
        runner = Runner("192.168.4.75", "192.168.4.75", "luke", call=Mock())
        with patch("scripts.network_resilience.harness.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(HarnessError, "interactive local TTY"):
                runner.remote("sudo systemd-run", tty=True, sudo=True)
        runner.call.assert_not_called()

    def test_suite_stops_after_first_failed_case_without_stacking(self):
        runner = FakeRunner()
        args = SimpleNamespace(host=runner.host, expected_ip="192.168.4.66", suite="dhcp", cycles=2,
                               cases=["reconnect", "link-bounce"], allow_address_transition=False)
        suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
        suite.baseline = Mock()
        suite.disruptive = Mock(side_effect=HarnessError("first case failed"))
        with self.assertRaisesRegex(HarnessError, "first case failed"):
            suite.run()
        suite.disruptive.assert_called_once_with("reconnect", "192.168.4.66")

    def test_uncertain_watchdog_scheduling_never_starts_disruption(self):
        for observable in (True, False):
            class LostReply(FakeRunner):
                def remote(self, command, **kwargs):
                    if command.startswith("sudo systemd-run"):
                        self.commands.append((command, kwargs))
                        raise TransportError("SSH reply lost after possible timer acceptance")
                    if command.startswith("systemctl is-active") and not observable:
                        self.commands.append((command, kwargs))
                        raise TransportError("host still unreachable")
                    return super().remote(command, **kwargs)
            runner = LostReply()
            args = SimpleNamespace(suite="dhcp")
            suite = Suite(runner, args, clock=lambda: 0, sleep=lambda _: None)
            suite.baseline("192.168.4.66")
            with self.subTest(observable=observable), self.assertRaisesRegex(HarnessError, "outcome uncertain"):
                suite.disruptive("reconnect", "192.168.4.66")
            self.assertEqual(suite.recovery_state, "pending" if observable else "unknown_possible_pending")
            self.assertIn("recovery-01", suite.report["watchdog_unit"])
            schedules = [c for c, _ in runner.commands if c.startswith("sudo systemd-run")]
            self.assertEqual(len(schedules), 1)
            self.assertNotIn("-reconnect-01", schedules[0])
            self.assertFalse(any("systemctl stop" in c for c, _ in runner.commands))

    def test_roundtrip_refuses_stale_profile_or_system_before_arming(self):
        for case in ("static_roundtrip", "generation_roundtrip"):
            for field in ("connection_uuid", "system_path"):
                original = self.static_probe()
                changed = self.static_probe()
                changed[field] = ("d63ad997-9ab2-55d0-bc18-015b25571456" if field == "connection_uuid"
                                  else original["system_path"] + "-changed")
                runner = FakeRunner(probes=[original, changed])
                suite = Suite(runner, SimpleNamespace(suite="static"), clock=lambda: 0)
                suite.baseline("192.168.4.66", method="manual")
                with self.subTest(case=case, field=field), self.assertRaisesRegex(HarnessError, "changed since baseline"):
                    getattr(suite, case)("192.168.4.66")
                self.assertFalse(any(c.startswith("sudo ") for c, _ in runner.commands))
                self.assertEqual(suite.recovery_state, "not_armed")

    def test_roundtrip_refuses_changed_interface_before_arming(self):
        for case in ("static_roundtrip", "generation_roundtrip"):
            original = self.static_probe()
            renamed = self.static_probe()
            renamed["macs"]["renamed0"] = renamed["macs"].pop("enp2s2")
            renamed["addresses"][0]["interface"] = "renamed0"
            renamed["default_routes"][0]["device"] = "renamed0"
            runner = FakeRunner(probes=[original, renamed])
            suite = Suite(runner, SimpleNamespace(suite="static"))
            suite.baseline("192.168.4.66", method="manual")
            with self.subTest(case=case), self.assertRaisesRegex(HarnessError, "interface changed"):
                getattr(suite, case)("192.168.4.66")
            self.assertFalse(any(c.startswith("sudo ") for c, _ in runner.commands))

    def test_watchdog_cancellation_requires_confirmed_inactive_timer(self):
        for state in ("activating", "deactivating", "failed", ""):
            class UnconfirmedTimer(FakeRunner):
                def remote(self, command, **kwargs):
                    if command.startswith("systemctl show") and ".timer " in command:
                        return state + "\n"
                    return super().remote(command, **kwargs)
            runner = UnconfirmedTimer()
            suite = Suite(runner, SimpleNamespace(suite="dhcp"))
            with self.subTest(state=state), self.assertRaisesRegex(HarnessError, "not confirmed inactive"):
                suite._cancel_watchdog(suite.unit("watchdog"))
            self.assertEqual(suite.recovery_state, "cancellation_unconfirmed")
            stops = [c for c, _ in runner.commands if c.startswith("sudo systemctl stop")]
            self.assertTrue(all(".service" not in c for c in stops))

    def test_watchdog_properties_are_parsed_by_name_not_dbus_order(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"))
        self.assertEqual(suite._unit_state("test-unit"), ("inactive", "dead", "success", "0"))
        self.assertNotIn("--value", runner.commands[-1][0])

    def test_cancel_confirms_inactive_timer_without_is_active_exit_code_trap(self):
        class NativeExitCodes(FakeRunner):
            def remote(self, command, **kwargs):
                if command.startswith("systemctl is-active"):
                    raise RemoteCommandError("inactive timers return status 3")
                return super().remote(command, **kwargs)
        runner = NativeExitCodes()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"))
        suite._cancel_watchdog(suite.unit("watchdog"))
        self.assertEqual(suite.recovery_state, "cancelled")
        self.assertFalse(any(c.startswith("systemctl is-active") for c, _ in runner.commands))

    def test_detached_commands_resolve_tools_with_no_inherited_login_path(self):
        # Execute only fake tools, never actual ip/nmcli/systemctl. Reproduce the
        # clean service environment that revealed the first live runner failure.
        with tempfile.TemporaryDirectory(prefix="network-job-path-") as temporary:
            root = Path(temporary)
            log = root / "calls.jsonl"
            for name in ("nmcli", "ip", "sleep", "systemctl"):
                executable = root / name
                executable.write_text(
                    f"#!{sys.executable}\nimport json, os, sys\n"
                    "with open(os.environ['FAKE_NETWORK_LOG'], 'a') as f:\n"
                    "    f.write(json.dumps(sys.argv) + '\\n')\n"
                )
                executable.chmod(0o755)
            runner = FakeRunner()
            suite = Suite(runner, SimpleNamespace(suite="dhcp"))
            command = "set -e; nmcli connection show; sleep 2; ip link show; systemctl is-active NetworkManager"
            with patch("scripts.network_resilience.harness.SYSTEM_JOB_PATH", str(root)):
                suite._schedule(suite.unit("action"), command)
            arguments = shlex.split(runner.commands[-1][0])
            setting = next(x for x in arguments if x.startswith("--setenv=PATH="))
            environment = {"PATH": setting.removeprefix("--setenv=PATH="), "FAKE_NETWORK_LOG": str(log)}
            shell = arguments.index("--") + 1
            result = subprocess.run(arguments[shell:], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([Path(call[0]).name for call in calls], ["nmcli", "sleep", "ip", "systemctl"])
            self.assertNotIn(os.environ.get("HOME", "unavailable-home"), setting)

    def test_completed_unit_collection_during_cleanup_is_not_failure(self):
        class CollectedDuringStop(FakeRunner):
            def remote(self, command, **kwargs):
                result = super().remote(command, **kwargs)
                if command.startswith("sudo systemctl stop"):
                    raise RemoteCommandError("remote command exited 5: transient unit already collected")
                return result
        runner = CollectedDuringStop()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"))
        unit = suite.unit("finished")
        suite.completed_jobs.add(unit)
        suite._cleanup_job(unit)
        self.assertNotIn(unit, suite.completed_jobs)
        stops = [c for c, _ in runner.commands if c.startswith("sudo systemctl stop")]
        self.assertEqual(len(stops), 1)
        self.assertLess(stops[0].index(".timer"), stops[0].index(".service"))

    def test_failed_cleanup_cannot_cancel_or_obscure_an_active_job(self):
        class StopDenied(FakeRunner):
            def remote(self, command, **kwargs):
                if command.startswith("sudo systemctl stop"):
                    raise RemoteCommandError("remote command exited 1: authorization failed")
                return super().remote(command, **kwargs)
        runner = StopDenied()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"))
        unit = suite.unit("finished")
        suite.completed_jobs.add(unit)
        with self.assertRaises(RemoteCommandError):
            suite._cleanup_job(unit)
        self.assertIn(unit, suite.completed_jobs)
        with self.assertRaisesRegex(HarnessError, "unverified job"):
            suite._cleanup_job(suite.unit("not-verified"))

    def test_watchdog_is_cancelled_before_cosmetic_action_unit_cleanup(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"), clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("reconnect", "192.168.4.66")
        stops = [c for c, _ in runner.commands if c.startswith("sudo systemctl stop")]
        self.assertIn("-recovery-01.timer", stops[0])
        self.assertIn("-reconnect-01.timer", stops[1])
        self.assertEqual(suite.recovery_state, "cancelled")

    def test_job_plumbing_uses_same_job_lifecycle_without_network_changes(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"), clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("job-plumbing", "192.168.4.66")
        schedules = [shlex.split(c) for c, _ in runner.commands if c.startswith("sudo systemd-run")]
        self.assertEqual(len(schedules), 2)
        bodies = [args[-1] for args in schedules]
        self.assertEqual(bodies[0], "true")
        self.assertIn("command -v nmcli ip systemctl sleep", bodies[1])
        for body in bodies:
            self.assertNotIn("connection up", body)
            self.assertNotIn("disconnect", body)
            self.assertNotIn("restart", body)
            self.assertNotIn("ip link", body)
        self.assertFalse(suite.report["cases"][-1]["network_changed"])
        self.assertEqual(suite.recovery_state, "cancelled")

    def _execute_activation_with_fake_carrier(self, ready_after):
        with tempfile.TemporaryDirectory(prefix="network-carrier-") as temporary:
            root = Path(temporary)
            log = root / "calls.jsonl"
            count = root / "carrier-count"
            fake = (
                f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
                "with open(os.environ['FAKE_NETWORK_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv) + '\\n')\n"
                "if 'WIRED-PROPERTIES.CARRIER' in sys.argv:\n"
                "    p = pathlib.Path(os.environ['FAKE_CARRIER_COUNT'])\n"
                "    attempts = int(p.read_text()) + 1 if p.exists() else 1\n"
                "    p.write_text(str(attempts))\n"
                "    print('on' if attempts > int(os.environ['FAKE_READY_AFTER']) else 'off')\n"
            )
            for name in ("nmcli", "ip", "sleep"):
                tool = root / name
                tool.write_text(fake)
                tool.chmod(0o755)
            env = {"PATH": str(root), "FAKE_NETWORK_LOG": str(log),
                   "FAKE_CARRIER_COUNT": str(count), "FAKE_READY_AFTER": str(ready_after)}
            command = "set -e; " + activation_command("synthetic0", UUID)
            result = subprocess.run(["/bin/sh", "-c", command], env=env, capture_output=True, text=True, timeout=15)
            return result, [json.loads(line) for line in log.read_text().splitlines()]

    def test_activation_waits_for_nm_carrier_before_connecting(self):
        result, calls = self._execute_activation_with_fake_carrier(ready_after=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        queries = [i for i, call in enumerate(calls) if "WIRED-PROPERTIES.CARRIER" in call]
        activations = [i for i, call in enumerate(calls) if "connection" in call and "up" in call]
        self.assertEqual(len(queries), 3)
        self.assertEqual(len(activations), 1)
        self.assertLess(queries[-1], activations[0])
        self.assertIn("60", calls[activations[0]])
        self.assertIn(UUID, calls[activations[0]])

    def test_missing_carrier_fails_bounded_without_attempting_activation(self):
        result, calls = self._execute_activation_with_fake_carrier(ready_after=999)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ethernet carrier did not return", result.stderr)
        self.assertEqual(sum("WIRED-PROPERTIES.CARRIER" in call for call in calls), 30)
        self.assertFalse(any("connection" in call and "up" in call for call in calls))

    def test_link_bounce_and_its_watchdog_both_wait_for_carrier(self):
        runner = FakeRunner()
        suite = Suite(runner, SimpleNamespace(suite="dhcp"), clock=lambda: 0, sleep=lambda _: None)
        suite.baseline("192.168.4.66")
        suite.disruptive("link-bounce", "192.168.4.66")
        bodies = [shlex.split(c)[-1] for c, _ in runner.commands if c.startswith("sudo systemd-run")]
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            self.assertIn("WIRED-PROPERTIES.CARRIER", body)
            self.assertLess(body.index("WIRED-PROPERTIES.CARRIER"), body.index("connection up uuid"))
            self.assertIn("nmcli --wait 60 connection up uuid " + UUID, body)

    def test_invalid_ipv6_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            valid_ip("::1")


if __name__ == "__main__":
    unittest.main()
