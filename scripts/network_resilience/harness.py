"""Conservative Mac-driven SSH harness for DHCP reservation resilience checks."""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shlex
import subprocess
import sys
import time
import uuid as uuidlib
from dataclasses import dataclass
from typing import Callable


# System services do not inherit the interactive NixOS login shell's PATH.
# Both action and recovery jobs must resolve only root-owned system tools.
SYSTEM_JOB_PATH = "/run/current-system/sw/bin:/run/wrappers/bin"

# Administrative link-up returns before Ethernet negotiation and NM's carrier
# state are necessarily ready. Keep retries and activation individually bounded;
# the enclosing systemd TimeoutStartSec also bounds the entire operation.
CARRIER_WAIT = (
    'carrier_attempt=0; '
    'until [ "$(LC_ALL=C nmcli --wait 5 -g WIRED-PROPERTIES.CARRIER device show @INTERFACE@)" = on ]; do '
    'carrier_attempt=$((carrier_attempt + 1)); '
    'if [ "$carrier_attempt" -ge 30 ]; then '
    'printf "%s\\n" "Ethernet carrier did not return" >&2; exit 1; fi; '
    'sleep 1; done; '
)


def activation_command(interface: str, connection: str) -> str:
    target = shlex.quote(interface)
    return ("ip link set dev " + target + " up; "
            + CARRIER_WAIT.replace("@INTERFACE@", target)
            + "nmcli --wait 60 connection up uuid " + shlex.quote(connection) + " ifname " + target)


PROBE_SCRIPT = r'''import json,os,re,subprocess,sys

def run(args):
 try:
  q=subprocess.run(args,text=True,capture_output=True,timeout=6)
  return q.stdout[:8192]
 except Exception:return ""
def lines(args): return run(args).splitlines()
def first(args): return next((x.strip() for x in lines(args) if x.strip()), "")
rows=[]
for line in lines(["ip","-o","-4","addr","show"]):
 p=line.split()
 if len(p)>3 and p[2]=="inet": rows.append({"interface":p[1],"address":p[3]})
mac={name:first(["cat","/sys/class/net/"+name+"/address"]) for name in os.listdir("/sys/class/net")}
lan=next((name for name,value in mac.items() if value.lower()=="00:e0:4c:5b:96:3e"),"")
routes=[]
for route in lines(["ip","-4","route","show","default"]):
 p=route.split(); routes.append({"device":p[p.index("dev")+1] if "dev" in p else "","gateway":p[p.index("via")+1] if "via" in p else ""})
default=next((r for r in routes if r["device"]==lan),{})
dns_values=[]
for line in lines(["nmcli","-t","-f","GENERAL.DEVICE,IP4.DNS","device","show",lan]):
 key,sep,value=line.partition(":")
 if sep and re.fullmatch(r"IP4\.DNS(?:\[\d+\])?",key) and value.strip(): dns_values.append(value.strip())
try:
 q=subprocess.run(["getent","ahostsv4","github.com"],capture_output=True,text=True,timeout=5); resolution=q.returncode==0 and bool(q.stdout.strip())
except Exception: resolution=False
recovery_id=sys.argv[1] if len(sys.argv)>1 else "zimaboard-lan-dhcp"
active_rows=lines(["nmcli","-t","-f","UUID,TYPE,DEVICE","connection","show","--active"]); active=""
for line in active_rows:
 fields=line.split(":")
 if len(fields)>=3 and fields[2]==lan: active=fields[0]; break
method=first(["nmcli","-g","ipv4.method","connection","show","uuid",active]) if active else ""
recovery_uuids=lines(["nmcli","-g","connection.uuid","connection","show","id",recovery_id])
profiles=[]
for profile_uuid in dict.fromkeys(([active] if active else [])+recovery_uuids):
 if not profile_uuid: continue
 def attr(key): return first(["nmcli","-g",key,"connection","show","uuid",profile_uuid])
 profiles.append({"id":attr("connection.id"),"uuid":profile_uuid,"method":attr("ipv4.method"),"mac":attr("802-3-ethernet.mac-address")})
services={s:first(["systemctl","is-active",s]) for s in ("NetworkManager","sshd","caddy")}
try:
 import urllib.request
 with urllib.request.urlopen("http://127.0.0.1:9000/",timeout=4) as r: http=r.status
except Exception as e: http=getattr(e,"code",0) or 0
print(json.dumps({"addresses":rows,"macs":mac,"gateway":default.get("gateway",""),"default_routes":routes,"dns_values":dns_values,"dns_present":bool(dns_values),"name_resolution":resolution,"connection_uuid":active,"ipv4_method":method,"profiles":profiles,"boot_id":first(["cat","/proc/sys/kernel/random/boot_id"]),"python_path":os.path.realpath(sys.executable),"system_path":first(["readlink","-f","/run/current-system"]),"services":services,"peertube_http":http}))'''
PROBE = "python3 -c " + shlex.quote(PROBE_SCRIPT)


class HarnessError(RuntimeError):
    pass


class TransportError(HarnessError):
    """SSH is temporarily unreachable; safe to retry read-only observation."""


class RemoteCommandError(HarnessError):
    """SSH connected but the requested remote command failed."""


def valid_ip(value: str) -> str:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an IPv4 address") from exc
    if parsed.version != 4:
        raise argparse.ArgumentTypeError("must be an IPv4 address")
    return str(parsed)


def valid_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise argparse.ArgumentTypeError("invalid SSH user/host/interface")
    return value


def valid_system_path(value: str) -> bool:
    return bool(re.fullmatch(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-nixos-system-[A-Za-z0-9._+-]+", value))


def system_path_arg(value: str) -> str:
    if not valid_system_path(value):
        raise argparse.ArgumentTypeError("must be an absolute /nix/store/...-nixos-system-... path")
    return value


def valid_python_path(value: str) -> bool:
    return bool(re.fullmatch(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9._+-]+/bin/python3(?:\.[0-9]+)*", value))


def ssh_args(host: str, alias: str, user: str, tty: bool = False) -> list[str]:
    args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "CheckHostIP=no", "-o", "ConnectTimeout=8", "-o", "HostKeyAlias=" + alias]
    if tty:
        args.append("-tt")
    return args + [f"{user}@{host}"]


@dataclass
class Runner:
    host: str
    alias: str
    user: str
    timeout: int = 20
    call: Callable = subprocess.run
    probe_hosts: tuple[str, ...] = ()
    recovery_profile: str = "zimaboard-lan-dhcp"

    def remote(self, command: str, *, tty: bool = False, sudo: bool = False,
               fallback: bool = False, host: str | None = None) -> str:
        if sudo and not sys.stdin.isatty():
            raise HarnessError("disruption requires an interactive local TTY for remote sudo")
        hosts = tuple(dict.fromkeys((self.host, *self.probe_hosts))) if fallback else (host or self.host,)
        failures = []
        for target in hosts:
            cmd = ssh_args(target, self.alias, self.user, tty=tty) + [command]
            try:
                result = self.call(cmd, input=None, capture_output=not tty, text=True, timeout=self.timeout)
            except (subprocess.TimeoutExpired, OSError) as exc:
                failures.append(f"SSH transport error for {target}: {type(exc).__name__}")
                continue
            if result.returncode == 255:
                failures.append(f"SSH transport failed for {target}")
                continue
            if result.returncode:
                detail = (getattr(result, "stderr", "") or "").strip()[:250]
                raise RemoteCommandError(f"remote command exited {result.returncode}" + (f": {detail}" if detail else ""))
            return getattr(result, "stdout", "") or ""
        raise TransportError("; ".join(failures)[:500])

    def probe(self) -> dict:
        failures = []
        for target in tuple(dict.fromkeys((self.host, *self.probe_hosts))):
            try:
                command = "python3 -c " + shlex.quote(PROBE_SCRIPT) + " " + shlex.quote(self.recovery_profile)
                result = json.loads(self.remote(command, host=target))
            except TransportError as exc:
                failures.append(str(exc))
                continue
            except json.JSONDecodeError as exc:
                raise HarnessError("read-only probe returned invalid JSON") from exc
            self.host = target
            return result
        raise TransportError("read-only probe unavailable: " + "; ".join(failures)[:400])


def health(probe: dict, expected_ip: str, *, transition: bool = False, original_ip: str | None = None,
           method: str = "auto", connection_uuid: str | None = None) -> list[str]:
    errors = []
    lan = next((name for name, mac in probe.get("macs", {}).items()
                if mac.lower() == "00:e0:4c:5b:96:3e"), None)
    permitted = {expected_ip, original_ip} if transition and original_ip else {expected_ip}
    addresses = []
    for row in probe.get("addresses", []):
        try: addresses.append((row.get("interface"), ipaddress.ip_interface(row["address"])))
        except (KeyError, TypeError, ValueError): pass
    if not any(name == lan and addr.ip.compressed in permitted and addr.network.prefixlen == 22
               for name, addr in addresses):
        errors.append("expected IPv4 address/prefix absent on LAN interface")
    if lan is None: errors.append("expected LAN MAC absent")
    if probe.get("gateway") != "192.168.4.1": errors.append("gateway mismatch")
    if not any(x.get("device") == lan and x.get("gateway") == "192.168.4.1"
               for x in probe.get("default_routes", [])):
        errors.append("LAN default route/gateway mismatch")
    if probe.get("ipv4_method") != method:
        errors.append("active connection is not " + ("DHCP/auto" if method == "auto" else "static/manual"))
    if not probe.get("connection_uuid"): errors.append("no active connection UUID")
    if connection_uuid and probe.get("connection_uuid") != connection_uuid: errors.append("active connection UUID mismatch")
    dns_values = set(probe.get("dns_values", []))
    if not probe.get("dns_present") or not probe.get("name_resolution"): errors.append("DNS health check failed")
    if method == "manual" and not {"192.168.4.1", "1.1.1.1"}.issubset(dns_values):
        errors.append("static DNS servers mismatch")
    if any(probe.get("services", {}).get(s) != "active" for s in ("NetworkManager", "sshd", "caddy")): errors.append("required service inactive")
    try:
        if not 200 <= int(probe.get("peertube_http", 0)) < 400: errors.append("PeerTube HTTP check failed")
    except (ValueError, TypeError): errors.append("PeerTube HTTP check failed")
    return errors


def spare_port_errors(probe: dict) -> list[str]:
    spare = next((name for name, mac in probe.get("macs", {}).items()
                  if mac.lower() == "00:e0:4c:5b:96:3f"), None)
    if spare is None: return ["spare physical MAC absent"]
    assigned = [row for row in probe.get("addresses", []) if row.get("interface") == spare]
    errors = []
    if assigned: errors.append("spare physical port has IPv4 address")
    if any(route.get("device") == spare for route in probe.get("default_routes", [])):
        errors.append("spare physical port has default route")
    return errors


class Suite:
    def __init__(self, runner: Runner, args, clock=time.monotonic, sleep=time.sleep):
        self.r, self.a, self.clock, self.sleep = runner, args, clock, sleep
        self.sequence = 0
        self.completed_jobs = set()
        self.run_id = uuidlib.uuid4().hex[:10]
        self.original_host = runner.host
        self.current_case = "baseline"
        self.recovery_state = "not_armed"
        self.report = {"host": getattr(args, "host", runner.host), "suite": args.suite, "cases": [], "status": "running"}

    def unit(self, label):
        return f"dhcp-resilience-{self.run_id}-{label}"

    def record(self, case, status, **facts):
        self.report["cases"].append({"case": case, "status": status, **facts})
        if status == "armed": self.recovery_state = "pending"
        elif case.endswith("rollback") and status == "verified": self.recovery_state = "verified"

    def _systemd(self, command: str):
        return self.r.remote("sudo " + command, tty=True, sudo=True)

    def _job_state(self, name: str) -> str:
        cmd = "systemctl show " + shlex.quote(name) + " -p ActiveState -p SubState -p Result --value"
        return self.r.remote(cmd, fallback=True).strip()

    def _unit_state(self, unit: str):
        cmd = ("systemctl show " + shlex.quote(unit + ".service")
               + " -p ActiveState -p SubState -p Result -p ExecMainStartTimestampMonotonic")
        properties = dict(line.split("=", 1) for line in self.r.remote(cmd, fallback=True).splitlines()
                          if "=" in line)
        required = ("ActiveState", "SubState", "Result", "ExecMainStartTimestampMonotonic")
        if not all(key in properties for key in required):
            raise HarnessError("incomplete watchdog service state; refusing cancellation")
        return tuple(properties[key] for key in required)

    def _schedule(self, unit: str, command: str, *, timer=False, delay=600, runtime=180):
        args = ["systemd-run", "--quiet", "--no-block", "--unit=" + unit,
                "--setenv=PATH=" + SYSTEM_JOB_PATH,
                "--property=Type=oneshot", "--property=RemainAfterExit=yes",
                "--property=TimeoutStartSec=" + str(runtime) + "s"]
        args += ["--on-active=" + str(delay if timer else 5) + "s",
                 "--timer-property=AccuracySec=1s", "--", "/bin/sh", "-c", command]
        self._systemd(" ".join(map(shlex.quote, args)))

    def _arm_watchdog(self, unit, schedule, label, **facts):
        # Even an SSH error can mean systemd accepted the timer before the reply
        # was lost. Record its identity BEFORE making the state-changing call.
        self.recovery_state = "unknown_possible_pending"
        self.report["watchdog_unit"] = unit
        self.record(label, "scheduling_unconfirmed", unit=unit)
        try:
            schedule()
        except HarnessError as exc:
            try:
                if self.r.remote("systemctl is-active " + shlex.quote(unit + ".timer"), fallback=True).strip() == "active":
                    self.record(label, "armed", unit=unit, scheduling_response="lost", **facts)
            except HarnessError:
                pass
            raise HarnessError(f"watchdog scheduling outcome uncertain; no disruption started; inspect {unit}: {exc}") from exc
        self.recovery_state = "arming_unverified"
        if self.r.remote("systemctl is-active " + shlex.quote(unit + ".timer"), fallback=True).strip() != "active":
            self.recovery_state = "arming_failed"
            raise HarnessError(f"recovery timer failed to arm: {unit}")
        self.record(label, "armed", unit=unit, **facts)

    def _check_captured_static(self, probe, expected):
        problems = health(probe, expected, method="manual", connection_uuid=self.connection)
        problems += spare_port_errors(probe)
        current_interface = next((name for name, mac in probe.get("macs", {}).items()
                                  if mac.lower() == "00:e0:4c:5b:96:3e"), None)
        if current_interface != self.interface:
            problems.append("LAN interface changed since baseline")
        if probe.get("boot_id") != self.boot_id:
            problems.append("boot changed since baseline")
        if probe.get("system_path") != self.system_path:
            problems.append("system generation changed since baseline")
        if problems:
            raise HarnessError("active configuration changed since baseline: " + ", ".join(problems))

    def _cancel_watchdog(self, unit: str):
        active, substate, result, started = self._unit_state(unit)
        if result in ("failed", "exit-code", "timeout"):
            self.recovery_state = "failed"
            raise HarnessError("watchdog service failed; it was not stopped")
        if (started and started != "0") or active in ("active", "activating", "reloading"):
            self.recovery_state = "active_not_cancelled"
            raise HarnessError("watchdog service has started; it was not stopped")
        self._systemd("systemctl stop " + shlex.quote(unit + ".timer"))
        # is-active exits nonzero for a correctly stopped timer. show does not.
        timer_state = self.r.remote("systemctl show " + shlex.quote(unit + ".timer")
                                    + " -p ActiveState --value").strip()
        if timer_state != "inactive":
            self.recovery_state = "cancellation_unconfirmed"
            raise HarnessError("watchdog timer was not confirmed inactive after stop request")
        active, substate, result, started = self._unit_state(unit)
        if result in ("failed", "exit-code", "timeout"):
            self.recovery_state = "failed"
            raise HarnessError("watchdog failed during cancellation; service was left running")
        if (started and started != "0") or active in ("active", "activating", "reloading"):
            self.recovery_state = "active_not_cancelled"
            raise HarnessError("watchdog started during cancellation; service was left running")
        self.recovery_state = "cancelled"

    def _cleanup_job(self, unit: str):
        if unit not in self.completed_jobs:
            raise HarnessError(f"refusing to clean unverified job {unit}")

        def inactive():
            return all(self.r.remote("systemctl show " + shlex.quote(unit + suffix)
                                     + " -p ActiveState --value").strip() == "inactive"
                       for suffix in (".timer", ".service"))

        if not inactive():
            try:
                # Stop the timer first. Stopping its completed service can make
                # systemd collect both transient units before the second stop.
                self._systemd("systemctl stop " + shlex.quote(unit + ".timer")
                              + " " + shlex.quote(unit + ".service"))
            except RemoteCommandError:
                # A collected unit may report 'not loaded'; accept only if both
                # units are now confirmed inactive, never hide an active job.
                if not inactive():
                    raise
        if not inactive():
            raise HarnessError(f"completed job cleanup was not confirmed: {unit}")
        self.completed_jobs.discard(unit)

    def baseline(self, expected, transition=False, method="auto"):
        p = self.r.probe()
        errs = health(p, expected, transition=transition, original_ip=self.original_host, method=method)
        if method == "manual": errs += spare_port_errors(p)
        if errs:
            raise HarnessError("baseline failed: " + ", ".join(errs))
        try:
            u = str(uuidlib.UUID(p["connection_uuid"]))
        except (KeyError, ValueError) as exc:
            raise HarnessError("invalid active connection UUID") from exc
        active_profiles = [x for x in p.get("profiles", []) if x.get("uuid") == u]
        if len(active_profiles) != 1 or active_profiles[0].get("method") != method:
            raise HarnessError("active profile method/UUID does not match requested phase")
        configured_mac = active_profiles[0].get("mac", "").lower()
        if configured_mac and configured_mac != "00:e0:4c:5b:96:3e":
            raise HarnessError("active profile is pinned to a different MAC")
        self.interface = next(name for name, mac in p["macs"].items()
                              if mac.lower() == "00:e0:4c:5b:96:3e")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.interface):
            raise HarnessError("invalid discovered interface")
        self.connection = u
        self.method = method
        self.boot_id = p.get("boot_id")
        self.system_path = p.get("system_path")
        lan_ips = [x.get("address", "").split("/", 1)[0] for x in p.get("addresses", [])
                   if x.get("interface") == self.interface]
        observed = expected if expected in lan_ips else self.original_host if transition and self.original_host in lan_ips else ""
        self.current_case = "baseline"
        status = "transition_pending" if observed != expected else "verified"
        self.record("baseline", status, expected_ip=expected, observed_ip=observed, interface=self.interface,
                    mac="00:e0:4c:5b:96:3e", ipv4_method=method, services="active", dns="verified", peertube="verified")

    def _profile_uuid(self, probe, profile_id, method):
        matches = [x for x in probe.get("profiles", []) if x.get("id") == profile_id]
        if len(matches) != 1:
            raise HarnessError(f"required connection profile {profile_id!r} is not uniquely available")
        item = matches[0]
        try:
            parsed = str(uuidlib.UUID(item["uuid"]))
        except (KeyError, ValueError) as exc:
            raise HarnessError("recovery profile has invalid UUID") from exc
        if item.get("method") != method or item.get("mac", "").lower() != "00:e0:4c:5b:96:3e":
            raise HarnessError("recovery profile method or LAN MAC mismatch")
        return parsed

    def _wait_job(self, unit, timeout=360):
        deadline = self.clock() + timeout
        last_transport = ""
        while self.clock() < deadline:
            try:
                state = self._job_state(unit + ".service")
            except TransportError as exc:
                last_transport = str(exc)
                self.sleep(2)
                continue
            if "failed" in state or "failure" in state:
                raise HarnessError(f"transient job {unit} failed")
            if "success" in state and "exited" in state:
                self.completed_jobs.add(unit)
                return
            self.sleep(2)
        raise HarnessError(f"transient job {unit} completion timed out" + (": " + last_transport if last_transport else ""))

    def static_roundtrip(self, expected):
        p = self.r.probe()
        self._check_captured_static(p, expected)
        dhcp_uuid = self._profile_uuid(p, self.a.dhcp_profile, "auto")
        self.record("dhcp-roundtrip-preflight", "verified", dhcp_profile=self.a.dhcp_profile)
        watchdog = self.unit("static-rollback")
        recovery = "set -e; systemctl start NetworkManager; " + activation_command(self.interface, self.connection)
        self._arm_watchdog(watchdog,
                           lambda: self._schedule(watchdog, recovery, timer=True, delay=900, runtime=180),
                           "dhcp-roundtrip-watchdog", rollback_target="captured static UUID")
        dhcp_job = self.unit("roundtrip-dhcp")
        try:
            self._schedule(dhcp_job, "set -e; " + activation_command(self.interface, dhcp_uuid), runtime=180)
            self._wait_job(dhcp_job, 210)
            self._wait(expected, method="auto", connection_uuid=dhcp_uuid)
            self.record("dhcp-roundtrip-dhcp", "verified", unit=dhcp_job, method="auto", connection_uuid=dhcp_uuid)
        except HarnessError as exc:
            self.record("dhcp-roundtrip-dhcp", "failed", unit=dhcp_job, error=str(exc)[:300], recovery="watchdog_pending")
            raise
        static_job = self.unit("roundtrip-static")
        try:
            self._schedule(static_job, "set -e; " + activation_command(self.interface, self.connection), runtime=180)
            self._wait_job(static_job, 210)
            self._wait(expected, method="manual", connection_uuid=self.connection)
        except HarnessError as exc:
            self.record("dhcp-roundtrip-static", "failed", unit=static_job, error=str(exc)[:300], recovery="watchdog_pending")
            raise
        self.record("dhcp-roundtrip-static", "verified", unit=static_job, method="manual", connection_uuid=self.connection)
        try:
            self._cancel_watchdog(watchdog)
        except HarnessError as exc:
            raise HarnessError(f"static state verified but watchdog cleanup not confirmed: {exc}") from exc
        self.record("dhcp-roundtrip-rollback", "verified", static_restored=True, watchdog="cancelled")
        self._cleanup_job(dhcp_job)
        self._cleanup_job(static_job)

    def _validate_store_system(self, path):
        if not valid_system_path(path): raise HarnessError("system path must be a canonical Nix store system path")
        command = "test \"$(readlink -f " + shlex.quote(path) + ")\" = " + shlex.quote(path) + " && test -x " + shlex.quote(path + "/bin/switch-to-configuration")
        self.r.remote(command)

    def _validate_python_path(self, path):
        if not valid_python_path(path): raise HarnessError("board Python must be a canonical Nix store interpreter")
        self.r.remote("test \"$(readlink -f " + shlex.quote(path) + ")\" = " + shlex.quote(path)
                      + " && test -x " + shlex.quote(path))

    def _arm_generation_rollback(self, expected_current, python_path, unit):
        helper = r'''import os,re,subprocess,sys
expected,profile_uuid,interface,unit=sys.argv[1:]
current=os.path.realpath("/run/current-system")
pattern=r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-nixos-system-[A-Za-z0-9._+-]+"
if not re.fullmatch(pattern,current) or current != expected: raise SystemExit("current generation changed during preflight")
if not re.fullmatch(r"[0-9a-fA-F-]{36}",profile_uuid) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}",interface): raise SystemExit("invalid recovery identity")
exe=current+"/bin/switch-to-configuration"
if not os.path.isfile(exe) or not os.access(exe,os.X_OK): raise SystemExit("current switch helper unavailable")
import uuid
if str(uuid.UUID(profile_uuid)) != profile_uuid.lower(): raise SystemExit("invalid static profile UUID")
restore="set -e; "+repr(exe)+" test; systemctl start NetworkManager; ip link set dev "+interface+" up; "+CARRIER_WAIT.replace("@INTERFACE@",interface)+"nmcli --wait 60 connection up uuid "+profile_uuid+" ifname "+interface
if not re.fullmatch(r"dhcp-resilience-[a-f0-9]{10}-generation-rollback",unit): raise SystemExit("invalid timer unit")
cmd=["systemd-run","--quiet","--no-block","--unit="+unit,"--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin","--on-active=1800s","--timer-property=AccuracySec=1s","--property=Type=oneshot","--property=RemainAfterExit=yes","--property=TimeoutStartSec=420s","--","/bin/sh","-c",restore]
subprocess.run(cmd,check=True,timeout=20)'''
        helper = "CARRIER_WAIT = " + repr(CARRIER_WAIT) + "\n" + helper
        command = shlex.quote(python_path) + " -c " + shlex.quote(helper) + " " + " ".join(
            shlex.quote(x) for x in (expected_current, self.connection, self.interface, unit))
        self._systemd(command)

    def generation_roundtrip(self, expected):
        p = self.r.probe()
        self._check_captured_static(p, expected)
        previous = self.a.previous_system
        self._validate_store_system(previous)
        current = p.get("system_path", "")
        python_path = p.get("python_path", "")
        self._validate_store_system(current)
        self._validate_python_path(python_path)
        if current == previous: raise HarnessError("previous system path must differ from current system")
        dhcp_uuid = self._profile_uuid(p, self.a.dhcp_profile, "auto")
        watchdog = self.unit("generation-rollback")
        self._arm_watchdog(watchdog,
                           lambda: self._arm_generation_rollback(current, python_path, watchdog),
                           "generation-watchdog", restore_target="captured current generation and static UUID")
        previous_job = self.unit("generation-previous")
        try:
            action = ("set -e; " + shlex.quote(previous + "/bin/switch-to-configuration")
                      + " test; systemctl start NetworkManager; " + activation_command(self.interface, dhcp_uuid))
            self._schedule(previous_job, action, runtime=420)
            self._wait_job(previous_job, 450)
            self._wait(expected, method="auto", connection_uuid=dhcp_uuid, system_path=previous)
            self.record("generation-previous", "verified", unit=previous_job, generation_changed=True, method="auto")
        except HarnessError as exc:
            self.record("generation-previous", "failed", unit=previous_job, error=str(exc)[:300], recovery="watchdog_pending")
            raise
        restore_job = self.unit("generation-restore")
        restore = ("set -e; " + shlex.quote(current + "/bin/switch-to-configuration")
                   + " test; systemctl start NetworkManager; " + activation_command(self.interface, self.connection))
        try:
            self._schedule(restore_job, restore, runtime=420)
            self._wait_job(restore_job, 450)
            self._wait(expected, method="manual", connection_uuid=self.connection, system_path=current)
        except HarnessError as exc:
            self.record("generation-restore", "failed", unit=restore_job, error=str(exc)[:300], recovery="watchdog_pending")
            raise
        self.record("generation-restore", "verified", unit=restore_job, generation_restored=True, method="manual")
        try:
            self._cancel_watchdog(watchdog)
        except HarnessError as exc:
            raise HarnessError(f"current generation restored but watchdog cleanup not confirmed: {exc}") from exc
        self.record("generation-rollback", "verified", watchdog="cancelled")
        self._cleanup_job(previous_job)
        self._cleanup_job(restore_job)

    def disruptive(self, case, expected, transition=False):
        self.sequence += 1
        self.current_case = case
        action_unit = self.unit(f"{case}-{self.sequence:02d}")
        if case == "reboot":
            self.recovery_state = "not_available_reboot"
            self._schedule(action_unit, "systemctl reboot", runtime=120)
            after = self._wait(expected, reboot=True, method=self.method, connection_uuid=self.connection)
            self.boot_id = after.get("boot_id")
            self.record(case, "verified", reboot_verified=True, recovery="no_timer_survives_reboot", unit=action_unit)
            return
        watchdog = self.unit(f"recovery-{self.sequence:02d}")
        iface = shlex.quote(self.interface)
        recovery = ("true" if case == "job-plumbing" else
                    "set -e; systemctl start NetworkManager; " + activation_command(self.interface, self.connection))
        self._arm_watchdog(watchdog,
                           lambda: self._schedule(watchdog, recovery, timer=True, delay=600, runtime=180),
                           case + "-watchdog",
                           restore_target=("no-op; no network changes" if case == "job-plumbing"
                                           else "captured active connection UUID"))
        action_prefix = "set -e; "
        if case == "job-plumbing":
            action = "set -e; command -v nmcli ip systemctl sleep >/dev/null; nmcli --version >/dev/null"
        elif case == "reconnect":
            action = (action_prefix + "nmcli device disconnect " + iface + "; sleep 2; "
                      + activation_command(self.interface, self.connection))
        elif case == "restart-networkmanager":
            action = action_prefix + "systemctl restart NetworkManager; " + activation_command(self.interface, self.connection)
        elif case == "link-bounce":
            action = (action_prefix + "ip link set dev " + iface + " down; sleep 2; "
                      + activation_command(self.interface, self.connection))
        else:
            raise HarnessError("unsupported disruptive case")
        self._schedule(action_unit, action, runtime=180)
        self._wait_job(action_unit, timeout=210)
        self._wait(expected, method=self.method, connection_uuid=self.connection)
        try:
            self._cancel_watchdog(watchdog)
        except HarnessError as exc:
            raise HarnessError(f"external validation passed but watchdog cleanup was not confirmed: {exc}") from exc
        self._cleanup_job(action_unit)
        self.record(case, "verified", ip=expected, watchdog="cancelled", unit=action_unit,
                    network_changed=case != "job-plumbing")

    def _wait(self, expected, reboot=False, method="auto", connection_uuid=None, system_path=None):
        deadline = self.clock() + 150
        last = "no probe"
        while self.clock() < deadline:
            try:
                p = self.r.probe()
                problems = health(p, expected, method=method, connection_uuid=connection_uuid)
                if method == "manual": problems += spare_port_errors(p)
                if system_path and p.get("system_path") != system_path: problems.append("system generation path mismatch")
                last = ", ".join(problems) or "healthy"
                if last == "healthy" and (not reboot or (p.get("boot_id") and p.get("boot_id") != self.boot_id)):
                    return p
            except TransportError as exc:
                last = str(exc)
            except RemoteCommandError:
                raise
            except HarnessError:
                raise
            self.sleep(3)
        raise HarnessError(f"post-action validation timed out: {last}")

    def run(self):
        self.report["operator_acknowledgements"] = {
            "reservation_confirmed": "acknowledged" if getattr(self.a, "reservation_confirmed", False) else "not_applicable",
            "console_available": "acknowledged_not_tested" if getattr(self.a, "console_available", False) else "not_applicable",
            "reboot_allowed": "acknowledged" if getattr(self.a, "allow_reboot", False) else "not_applicable",
            "system_switch_allowed": "acknowledged" if getattr(self.a, "allow_system_switch", False) else "not_applicable",
        }
        expected = self.a.expected_ip
        if self.a.suite == "dhcp":
            cases = ["reconnect", "restart-networkmanager", "link-bounce"] if self.a.cases is None else self.a.cases
            method = "auto"
        elif self.a.suite == "static":
            cases = ["restart-networkmanager", "link-bounce"] if self.a.cases is None else self.a.cases
            method = "manual"
        else:
            cases, method = [], "auto"
        transition = bool(cases and cases[0] == "reconnect" and self.a.allow_address_transition)
        self.baseline(expected, transition=transition, method=method)
        if self.a.suite == "static" and "dhcp-roundtrip" in cases:
            if len(cases) != 1: raise HarnessError("dhcp-roundtrip must run by itself")
            self.current_case = "dhcp-roundtrip"
            self.static_roundtrip(expected)
        elif self.a.suite == "static" and "generation-roundtrip" in cases:
            if len(cases) != 1: raise HarnessError("generation-roundtrip must run by itself")
            self.current_case = "generation-roundtrip"
            self.generation_roundtrip(expected)
        else:
            for _ in range(self.a.cycles):
                for case in cases:
                    self.disruptive(case, expected)
        self.report["status"] = "verified"


def parser():
    p = argparse.ArgumentParser(
        description="Opt-in network resilience checks. Generation tests use whole-system switch-to-configuration test and may affect Home Manager/services; no bootloader changes are performed. Reboot transient recovery timers do not survive reboot. Default and --dry-run are read-only.")
    p.add_argument("--host", type=valid_ip)
    p.add_argument("--expected-ip", type=valid_ip, default="192.168.4.66")
    p.add_argument("--host-key-alias")
    p.add_argument("--user", default="luke", type=valid_name)
    p.add_argument("--suite", choices=("smoke", "dhcp", "static"), default="smoke")
    p.add_argument("--cases", help="comma-separated cases: job-plumbing (no network changes), reconnect,restart-networkmanager,link-bounce,reboot; static also supports dhcp-roundtrip,generation-roundtrip")
    p.add_argument("--dhcp-profile", default="zimaboard-lan-dhcp", type=valid_name)
    p.add_argument("--previous-system", type=system_path_arg,
                   help="reviewed retained /nix/store system path for generation-roundtrip")
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--report")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-disruptive", action="store_true")
    p.add_argument("--reservation-confirmed", action="store_true")
    p.add_argument("--console-available", action="store_true",
                   help="operator acknowledgement; not proof or test of console availability")
    p.add_argument("--allow-reboot", action="store_true")
    p.add_argument("--allow-system-switch", action="store_true",
                   help="required for the whole-running-system generation round trip")
    p.add_argument("--allow-address-transition", action="store_true")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if not a.host: p.error("--host is required")
    rawcases = a.cases.split(",") if a.cases else None
    case_sets = {
        "smoke": {"baseline"},
        "dhcp": {"baseline", "job-plumbing", "reconnect", "restart-networkmanager", "link-bounce", "reboot"},
        "static": {"baseline", "job-plumbing", "reconnect", "restart-networkmanager", "link-bounce", "reboot", "dhcp-roundtrip", "generation-roundtrip"},
    }
    if rawcases is not None and (not rawcases or len(set(rawcases)) != len(rawcases)
                                 or any(c not in case_sets[a.suite] for c in rawcases)):
        p.error("--cases is invalid for the selected suite")
    cases = [c for c in (rawcases or []) if c != "baseline"]
    if a.cycles < 1 or a.cycles > 20: p.error("--cycles must be between 1 and 20")
    if a.suite == "dhcp" and rawcases is None: cases = ["reconnect", "restart-networkmanager", "link-bounce"]
    if a.suite == "static" and rawcases is None: cases = ["restart-networkmanager", "link-bounce"]
    if any(c in cases for c in ("dhcp-roundtrip", "generation-roundtrip")) and len(cases) != 1:
        p.error("round-trip cases must run by themselves")
    if "reboot" in cases and not a.allow_reboot: p.error("reboot requires --allow-reboot")
    if a.allow_reboot and "reboot" not in cases: p.error("--allow-reboot is only valid with reboot")
    if "generation-roundtrip" in cases:
        if not a.allow_system_switch or not a.previous_system:
            p.error("generation-roundtrip requires --allow-system-switch and --previous-system")
    elif a.allow_system_switch or a.previous_system:
        p.error("--allow-system-switch and --previous-system are only valid with generation-roundtrip")
    if a.allow_address_transition and not (a.suite == "dhcp" and cases and cases[0] == "reconnect"):
        p.error("--allow-address-transition only applies to the first DHCP reconnect")
    disruptive = bool(cases)
    if any(c in cases for c in ("dhcp-roundtrip", "generation-roundtrip")) and a.cycles != 1:
        p.error("round-trip cases do not support --cycles other than 1")
    try: alias = valid_name(a.host_key_alias or a.host)
    except argparse.ArgumentTypeError as exc: p.error(str(exc))
    planned = ["baseline"] + cases
    if a.dry_run:
        report = {"host": a.host, "suite": a.suite, "status": "dry-run", "planned_cases": planned,
                  "system_switch_warning": "whole running configuration may affect services" if "generation-roundtrip" in cases else None}
        output = json.dumps(report, indent=2)
        print(output)
        if a.report:
            with open(a.report, "w") as f: f.write(output + "\n")
        return 0
    if disruptive and not (a.allow_disruptive and a.reservation_confirmed and a.console_available):
        p.error("disruptions require --allow-disruptive --reservation-confirmed --console-available")
    a.cases = cases
    runner = Runner(a.host, alias, a.user, recovery_profile=a.dhcp_profile)
    runner.probe_hosts = (a.host, a.expected_ip)
    suite = Suite(runner, a)
    try:
        suite.run()
    except (HarnessError, KeyError, ValueError, StopIteration) as exc:
        suite.report["status"] = "failed"
        suite.report["error"] = str(exc)[:500]
        suite.report["failure"] = {"case": suite.current_case, "recovery": suite.recovery_state}
        print(str(exc)[:500], file=sys.stderr)
    except KeyboardInterrupt:
        suite.report["status"] = "interrupted"
        suite.report["failure"] = {"case": suite.current_case, "recovery": suite.recovery_state}
        suite.report["error"] = "operator interrupted; no pending recovery job was cancelled"
    suite.report["selected_host"] = runner.host
    suite.report["recovery_state"] = suite.recovery_state
    output = json.dumps(suite.report, indent=2)
    print(output)
    if a.report:
        with open(a.report, "w") as f: f.write(output + "\n")
    return 0 if suite.report["status"] == "verified" else 130 if suite.report["status"] == "interrupted" else 1
