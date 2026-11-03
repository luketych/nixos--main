# Opt-in network resilience tests

These are operator-run integration tests, separate from ordinary Python unit
and Nix evaluation checks. They deliberately interrupt the ZimaBoard's LAN
connection only when disruptive cases and their prerequisites are explicitly
selected. They do not configure eero, deploy a NixOS generation by default, or
prove that console recovery equipment is actually available.

**Live validation so far:** read-only smoke, `job-plumbing`, a DHCP reconnect,
and a NetworkManager restart have passed at `.66`, including watchdog
cancellation and cleanup. The board is using DHCP, not the prepared static
configuration. Earlier service-PATH and transient-cleanup bugs were fixed.

The first software link-bounce job failed because activation raced Ethernet
carrier negotiation. The board recovered automatically at `.66`; that run
still counts as a failure. After adding bounded carrier waits, a supervised
link-bounce rerun passed. One full reboot also passed with a changed boot ID.
Repeated complete groups, physical cable/router tests, watchdog execution,
static tests, and generation rollback have not been validated live. Console
access remains a prerequisite.

The dated [LAN investigation report](reports/2026-11-03-lan-resilience.md)
records the observations, current theories, proof limits, and escalation paths.

## Test phases

Run the DHCP phase first, before activating the static profile. A manually
assigned `.66` would hide whether eero honors its DHCP reservation.

| Phase | Checks | What a pass means |
| --- | --- | --- |
| Read-only smoke | SSH identity, correct NIC, address/method, gateway, DNS, services, local PeerTube HTTP | The observed configuration is healthy now; no fault was injected. |
| DHCP reservation | DHCP reconnect, NetworkManager restart, administrative link bounce; optional reboot | The correct NIC returns at the expected address using DHCP after each selected disturbance. |
| Static configuration | Manual-address checks, spare-port isolation, equivalent restart/link tests | The fixed address is configured manually, not accidentally supplied by DHCP. |
| DHCP recovery round trip | Static profile to explicit DHCP recovery profile and back | Both recovery and restoration work, with external reconnection checks at each phase. |
| Previous-generation round trip | Explicitly opt-in test activation of a reviewed previous generation and restoration | The selected rollback procedure restores connectivity; this changes the whole running NixOS configuration. |

The last two phases require an already installed, reviewed static profile and
an explicit DHCP recovery profile. Do not activate the unreviewed static branch
merely to make a test pass. See [networking.md](networking.md) for deployment
prerequisites and the console-assisted first activation.

## Running the group

Run from the repository root on the Mac with Python 3 and OpenSSH. Read-only
smoke checks need no sudo; disruptive cases prompt for remote sudo in a visible
terminal. SSH login must already work with a key; passwords are never supplied
by the harness. A default run targets expected address `.66` and requires DHCP.

Check the board's currently observed `.66` DHCP state (this is a snapshot, not
a new reservation or fault-injection test):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m scripts.network_resilience \
  --host 192.168.4.66 --host-key-alias zimaboard \
  --report /tmp/zimaboard-network-smoke.json
```

Preview DHCP cases without SSH, sudo, or fault injection:

```sh
python3 -m scripts.network_resilience --host 192.168.4.66 --host-key-alias zimaboard --suite dhcp --dry-run
```

After confirming the router reservation and recovery access, run the DHCP
suite from its current `.66` address. The acknowledgement flags record human
confirmation; they do not validate the router's settings or exercise the console.

```sh
python3 -m scripts.network_resilience --host 192.168.4.66 --host-key-alias zimaboard --suite dhcp \
  --cycles 3 \
  --allow-disruptive --reservation-confirmed --console-available \
  --report /tmp/zimaboard-dhcp-resilience.json
```

The DHCP group runs reconnect, NetworkManager restart, and software link bounce.
Reboot is never included implicitly. After basic tests pass, select it separately
with `--suite dhcp --cases reboot --allow-reboot` and the same three safety flags.
Static mode supports the same explicitly selected reboot case.

After the static profile has been separately reviewed and deployed, use
`--suite static --cases baseline` for read-only static/spare-port validation.
Use `--suite static` with safety flags to test NetworkManager restart and link
bounce, or `--suite static --cases reconnect` to explicitly reconnect the static
profile. These runs require `manual`, rather than `auto`, IPv4 configuration.
For the static-to-DHCP-to-static round trip:

```sh
python3 -m scripts.network_resilience --host 192.168.4.66 --host-key-alias zimaboard \
  --suite static --cases dhcp-roundtrip \
  --allow-disruptive --reservation-confirmed --console-available \
  --report /tmp/zimaboard-dhcp-roundtrip.json
```

The host-key alias must identify the SAME board key already trusted locally;
use the appropriate existing alias if it is not `zimaboard`. Verification
remains strict across candidate addresses. The default recovery profile is
`zimaboard-lan-dhcp`; `--dhcp-profile` can select another reviewed profile, but
it must be uniquely identified, use DHCP, and be pinned to the LAN MAC.

The full-generation test is **not part of either default group**. It requires
a retained, reviewed previous system path, all safety flags, and both
`--cases generation-roundtrip --allow-system-switch --previous-system <store-path>`
with `--suite static`. It activates that previous configuration with `test`,
verifies DHCP connectivity, and restores the captured current configuration and
static UUID. It affects the whole running system, including Home Manager and
services, and tests neither bootloader persistence nor console availability.
Round-trip cases run alone and do not accept repeated cycles.

The runner stops after the first failed/interrupted case and reports pending
recovery separately; successful recovery cannot turn a failed test into a pass.
Watchdogs run after 10 minutes for basic cases, 15 for the DHCP round trip,
and 30 for the generation round trip. These are recovery attempts, not a
connectivity guarantee. SSH commands, probes, and systemd startup are bounded;
complete sudo prompts promptly. Do not run concurrent controllers or immediately
retry after an uncertain outcome. Inspect the reported watchdog unit and use the
console if needed; never stop a recovery service that is already running.

Ordinary unit tests do not run live cases:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_network_resilience -v
```

### Check the detached-job environment before another live rehearsal

On NixOS, systemd jobs do not inherit the interactive SSH shell's PATH. Action
and recovery jobs explicitly set
`PATH=/run/current-system/sw/bin:/run/wrappers/bin`; their oneshot startup limit
uses `TimeoutStartSec` (`RuntimeMaxSec` does not bound oneshot startup).

After checking/cancelling stale timers from a failed run, exercise the entire
schedule/observe/cancel/cleanup lifecycle without changing networking:

```sh
python3 -m scripts.network_resilience --host 192.168.4.66 --host-key-alias zimaboard \
  --suite dhcp --cases job-plumbing \
  --allow-disruptive --reservation-confirmed --console-available \
  --report /tmp/zimaboard-job-plumbing.json
```

This case uses the same conservative authorization flags, but its action only
checks executable lookup and `nmcli --version`; its watchdog is a no-op. It
never reconnects or restarts networking. A pass verifies the job lifecycle,
not DHCP resilience. The following lower-level diagnostic checks only executable
lookup and does not exercise cleanup races:

```sh
ssh -t zimaboard \
  'sudo systemd-run --wait --pipe --collect --setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin /bin/sh -c "command -v nmcli ip systemctl sleep"'
```

It does not reconnect, restart, or change profiles. Use the current board
address if the SSH alias no longer targets the board.

## Required operator checks

Before any disruption:

- Verify in eero that `.66` is reserved for `00:e0:4c:5b:96:3e`, and check the
  router's client/lease list for conflicts. The suite records your acknowledgement;
  it does not independently verify router configuration.
- Confirm the actual console/recovery access available to you. Transient
  systemd recovery timers do **not** survive a host reboot.
- Exit Pi and other full-screen programs in SSH sessions that might disconnect.
- Allow time for downtime. NetworkManager restarts and host reboots can interrupt
  PeerTube and other users of the board.
- Check and trust the board's SSH host key before starting. Every candidate
  address must be verified against that same trusted identity; do not turn off
  host-key checking when the IP changes.
- Be present for any interactive sudo authorization. Do not add unrestricted
  passwordless sudo just to run the tests unattended.

## What is and is not automated

The Mac acts as an external observer, reconnecting over SSH after a selected
fault. Board-side disruptive operations must run in detached transient systemd
jobs, with recovery armed before the job starts. A process attached to SSH must
never run `connection down` and assume its next command will restore networking.

Each case checks the disturbance actually happened before claiming recovery:
a scheduled job merely being accepted is not a pass. A reboot requires a changed
boot ID. Test groups run sequentially and stop on the first failure; they must
not stack further faults while recovery is uncertain.

A software link bounce is **not** a physical cable/connector test. Real unplug/
replug testing and eero reboots remain manual, coordinated checks. Router outage
will normally interrupt Internet access even when a static LAN address survives.

Do not create intentional duplicate IPs, spoof another live client's MAC, or run
a rogue DHCP server on the home LAN. Such negative tests belong in an isolated
lab or VM network.

## Interpreting results

- A baseline pass is not evidence of reboot, lease renewal, or a reservation.
- A test failure followed by successful rollback is still a failed test; record
  recovery separately.
- A confirmed eero reservation and a console-availability acknowledgement are
  prerequisites, not machine-verified results.
- Local PeerTube HTTP and service checks do not prove eero's public port forwards
  or external DNS. Test PeerTube from outside the home LAN separately.
- DHCP may assign another address during recovery if the reservation is wrong.
  A runtime recovery attempt is not a guarantee of recovering the old IP.
- A static-to-DHCP connection round trip does not test a previous NixOS generation.
  Those are distinct test cases with different scopes and risks.

Reports should contain only bounded diagnostic facts and test outcomes, not
passwords, keys, environment dumps, Pi sessions, or response bodies. Keep local
reports outside Git (for example, under `/tmp`) unless deliberately reviewed.

### Analyze a passive SSH monitoring run

The optional read-only analyzer summarizes an existing monitoring run; it does
not start, stop, or modify the monitor or make network requests:

```sh
python3 -m scripts.network_resilience.analyze_monitor \
  "$HOME/.local/state/zimaboard-ssh-monitor/run-20261103T193927Z"
```

Add `--json` for machine-readable output. It separates SSH failures from
sampling gaps, flags inconsistent/incomplete inputs, and does not infer whether
an error was caused by the board, Mac, Wi-Fi, or router. In a running monitor,
summary and event counters may briefly differ because they are read separately;
retry the read-only analysis after the next sample if that occurs. Elapsed time
to the last event is not continuous coverage or a measured availability figure.
A completed window with gaps is not the same as every attempt succeeding.
