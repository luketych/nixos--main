# ZimaBoard LAN stability investigation

- **Report date:** 2026-11-03 UTC
- **Status:** evidence snapshot; not a reliability certification or deployment approval
- **Repository:** [luketych/nixos](https://github.com/luketych/nixos)
- **Companion evidence:** [reviewed JSON summary and source digests](2026-11-03-lan-resilience.evidence.json)
- **Follow-up:** [physical cable, eero reboot, and SSH sampling](2026-11-03-lan-physical-eero.md)

## Objective

Establish how the ZimaBoard obtains its LAN address, investigate why it had not
moved to the intended address, and test whether an eero DHCP reservation is
sufficient for stable addressing before introducing host-side static networking.

**ELI5:** The router can promise the board the same address every time it asks.
We checked that the board is actually asking the router, then interrupted the
connection and rebooted the board to see whether it came back at the promised
address. We also found and corrected problems in the test machinery itself.

This report covers the LAN investigation, candidate configuration, tests, and
operational implications. Earlier Pi/chezmoi installation work is background,
not independently revalidated by these network tests.

## Executive summary

- The board is currently reachable at **`192.168.4.66/22` via DHCP**, not a
  manually configured static address. NetworkManager reports `ipv4.method=auto`,
  and DHCP options identify server `192.168.4.1` and assigned address `.66`.
- Luke confirmed the eero reservation for active MAC `00:e0:4c:5b:96:3e`.
  The assistant did not independently read eero's configuration.
- Successful individual runs cover job plumbing, DHCP reconnect, NetworkManager
  restart, software link bounce, and a full reboot. Their final reports and
  limits are recorded below. This is not a repeated full-suite soak test.
- Several initial failures were test-harness problems, not evidence of an eero
  reservation failure. In particular, link-up does not mean carrier is ready.
- A declarative static configuration was prepared and evaluated, but **was not
  activated**. Its recovery and previous-generation tests have no live proof.
- Current evidence supports continuing with DHCP plus the reservation. That is
  a working recommendation, not a guarantee against future failures or a
  decision to discard the static draft.

## 1. Observed state and the distinction that matters

A read-only board snapshot was captured at **2026-11-03T06:56:40Z**:

| Property | Observed value |
| --- | --- |
| OS | NixOS `26.05.20261015.26f4107 (Yarara)` |
| Active interface | `enp2s0` |
| Active physical MAC | `00:e0:4c:5b:96:3e` |
| Active profile | `Wired connection 2` |
| Profile UUID | `0e4ea8c2-74ea-34a9-80fe-a80251d38aa2` |
| IPv4 address and method | `192.168.4.66/22`, `auto` |
| Gateway and configured IPv4 DNS | `192.168.4.1` |
| DHCP server identifier | `192.168.4.1` |
| DHCP client identifier | `01:00:e0:4c:5b:96:3e` |
| Observed lease duration | `14400` seconds; four hours at this observation |
| Active services | NetworkManager, sshd, caddy |
| Pending resilience timers | None |
| Candidate static/recovery profiles | Not installed |

The spare physical port was previously identified as `enp1s0`, MAC
`00:e0:4c:5b:96:3f`. The current DHCP tests do not prove spare-port isolation.

| Before this investigation | Current observed state |
| --- | --- |
| `.75` assigned via DHCP; `.66` was the documented target, not the live address. | `.66` assigned via DHCP; the manual static profile is still absent. |
| Commands sometimes used `.75` as both destination and stored SSH identity. | Operator uses SSH name `zimaboard`; the test runner targets `.66` with host-key alias `zimaboard`. |
| No exercised network fault-injection group in this checkout. | Opt-in harness with successful individual DHCP cases and explicit safety gates. |

**The important discriminator is `auto` versus `manual`, not the address alone.**
A host at `.66` could be using either DHCP or static configuration. Our observed
DHCP options and profile method establish which is in use here.

## 2. What we did

1. Inspected the live NetworkManager state and lease. The initial `.75` address
   was DHCP, with a four-hour lease observed. Waiting more than 24 hours does
   not, by itself, require a DHCP client to receive a different address.
2. Inspected the historical
   [LAN configuration](https://github.com/luketych/ZimaBoard2--_/blob/559f9ed68d1de34bd6847fff7976d446c8e39361/configs/networking/lan-ip.conf)
   and [helper documentation](https://github.com/luketych/ZimaBoard2--_/blob/559f9ed68d1de34bd6847fff7976d446c8e39361/host_setup/networking/README.md).
   They describe `.66/22`, the same active MAC, and a static-IP workaround that
   avoids relying on eero's DHCP address selection. They are evidence of intended
   configuration, not proof of a diagnosed router bug or successful old deployment.
3. A read-only shell check reported the old helper/config paths as absent or
   inaccessible, and the corresponding old static NetworkManager profile was
   absent. This did not establish whether copies exist elsewhere. Prepared native
   NixOS NetworkManager profiles in
   `hosts/zimaboard/networking.nix`, with documentation and evaluated tests.
   Profile representations were also accepted by the board's offline nmcli parser;
   this was not live activation or a complete system build.
4. Clarified the reservation/static distinction and chose to test DHCP first.
   Luke confirmed eero's `.66` reservation and availability of console access.
   The board was subsequently observed at `.66` with DHCP still enabled. The
   exact packet exchange or instant that caused that transition was not captured.
5. Standardized the Mac's operator-facing SSH name as `zimaboard`, preserving
   verified server identity across the address change. An SSH host-key alias is
   a local identity lookup name, not a second network destination or DNS service.
6. Implemented `scripts/network_resilience/`: external SSH observation, explicit
   fault selection, detached systemd jobs, runtime watchdogs, bounded checks,
   separate DHCP/static modes, reports, and isolated unit tests.
7. Debugged failures in that harness and ran the successful individual cases
   listed below. The assistant used read-only diagnostics; Luke supplied sudo
   authorization and executed the live privileged tests from the Mac.

No static profile, NixOS networking generation, eero API automation, new
management network, or passwordless-sudo policy was deployed during this phase.

## 3. Evidence ledger: what passed and what that means

All five retained final reports have top-level `status: verified` and a baseline
at `.66` with method `auto`. Artifact times below are **file modification times
in UTC**, not measured fault times, service downtime, or recovery-time targets.

| Case | Artifact time, 2026-11-03 UTC | Run ID | Established within this test |
| --- | --- | --- | --- |
| Job plumbing | 06:31:08 | `4075e6d961` | Harmless detached action, no-op watchdog, external checks, timer cancellation, and unit cleanup completed without changing networking. |
| DHCP reconnect | 06:36:10 | `ab4796f852` | A device disconnect followed by explicit reactivation of the captured profile returned at `.66` using DHCP; health checks and cleanup completed. |
| NetworkManager restart | 06:37:31 | `46b93b98f0` | Restart plus explicit link/profile reactivation returned at `.66` using DHCP; health checks and cleanup completed. |
| Software link bounce | 06:46:18 | `ab4eee9bfb` | Administrative down/up plus explicit profile reactivation returned at `.66`, after correcting the carrier-readiness race. |
| Full reboot | 06:49:09 | `9a7ca9f851` | Boot ID changed; the board subsequently passed DHCP/address/profile and health checks at `.66`. No post-boot profile activation was injected by the harness. |

**Assisted recovery is not the same as autonomous recovery.** The first three
network disturbances explicitly run link/profile activation commands. They
prove the tested recovery path works with that assistance, not that every
physical link outage or daemon restart will recover without assistance. The
reboot case provides separate, bounded evidence of automatic startup recovery.

### What the harness's health gate actually checks

After a successful action, the controller reconnects with strict SSH host-key
verification and requires:

- The expected active MAC/interface, `.66/22`, expected IPv4 method, and captured
  connection UUID. DHCP and static modes cannot silently substitute for each other.
- A default-route configuration for the LAN interface through `.1`, configured
  DNS values, and successful `getent ahostsv4 github.com` resolution.
- Active NetworkManager, sshd, and caddy services.
- A 2xx/3xx response from local PeerTube HTTP at `127.0.0.1:9000`.
- A completed transient action, rather than merely a successful scheduling request.
  Reboot additionally requires a changed boot ID.

This does **not** independently prove gateway packet forwarding, a cache-miss DNS
query, which DNS server answered, public HTTPS/port forwarding, video playback,
uploads, database integrity, or end-to-end Internet availability. Successful SSH
from the Mac does establish reachability of the tested LAN management path.

Non-reboot watchdogs were armed and then cancelled. **Their ability to perform
successful automatic recovery was not exercised.** Reboot reports correctly say
`not_available_reboot`: transient recovery timers do not survive host reboot.

## 4. Failures, corrections, and lessons

| Incident | Observed evidence | Correction and interpretation |
| --- | --- | --- |
| Detached job could not find tools | First reconnect action exited 127 with `nmcli: command not found`, before it disconnected anything. Its watchdog had the same environment weakness. | Set an explicit root-owned NixOS tool PATH for all action/recovery jobs; add clean-environment regression coverage. Login-shell success does not establish system-service readiness. |
| Cleanup failure after a working reconnect | The board returned at `.66`; the action was logged as completed/stopped, but the old runner reported generic `remote command failed` before cancelling recovery. | Confirm completed units are inactive, tolerate collection during cleanup only after that confirmation, and cancel recovery before cosmetic cleanup. The exact original remote exit status was not captured; transient-unit collection is a plausible explanation, not a conclusively demonstrated root cause. |
| Activation raced carrier readiness | Link-bounce action exited with nmcli activation failure: device had no carrier. The board later recovered at `.66`. | Wait for NetworkManager's carrier indication with bounded retries before activating the exact UUID; bound activation separately. Add delayed/missing-carrier tests. This did not establish a reservation failure. |
| Old output mistaken for a new test | The pasted JSON retained earlier run ID `09925938fa` and case `reconnect`; no new plumbing report existed then. | Match case/run ID and report path before interpreting results. Retain unique artifacts for future research. |
| Confusing SSH close messages | Each short privileged SSH session closed after submitting or cleaning a job. | A close line alone says neither network failure nor success. Inspect final JSON and job state. No unhandled failure should be inferred from an expected scheduler-session closure. |

In every failed run, the failure remains a failure even if the board was healthy
or later recovered. A successful retry does not retroactively turn the failed
execution into a pass.

## 5. Working model and theories

| Proposition | Confidence / basis | What would strengthen or contradict it |
| --- | --- | --- |
| The eero reservation is currently sufficient for this board's stable IPv4 address. | Supported by operator confirmation, observed DHCP lease/options, three assisted disturbance cases, and one actual reboot at `.66`. | Repeated cycles and normal renewals strengthen it. Reproducible assignment of another IP to the same identity would require investigation. |
| A reservation both directs DHCP assignment and keeps that server from allocating the address to another client. | Expected DHCP-server behavior, consistent with these observations. | We did not inspect eero's internal allocator or test other clients' allocation behavior. It cannot prevent another host from being manually configured with the same address. |
| The earlier delay was related to an existing lease/reservation taking effect rather than the host independently choosing new addresses. | Plausible, consistent with `.75` then `.66` while method remained `auto`. | Exact reservation-change timing and DHCP packet captures are missing. Do not claim a particular lease-renewal sequence was proven. |
| Correct physical-port identity matters more than interface name or hostname. | Two distinct physical MACs were observed; the active DHCP client identifier contains the active MAC. Historical configuration also deliberately pins that MAC. | Moving the cable to the other port changes the identity. The reservation does not automatically transfer to it. |
| Administrative link-up can precede usable Ethernet carrier. | Directly demonstrated by the failed link-bounce activation and later carrier recovery. | Driver/PHY timing and NetworkManager's view can differ. A physical cable test remains distinct from software down/up. |
| eero has a DHCP bug requiring host-side static addressing here. | **Not established.** Historical notes describe a workaround; current tests work with DHCP. | Requires a reproducible failure after validating identity/reservation, with DHCP/router evidence and competing causes excluded. |
| Strange terminal characters after disconnection are leftover terminal/application modes. | Plausible only. No matching incident report or character sample was found. | Capture the actual characters and whether typing, mouse input, or scrolling produces them. Do not conflate this with DHCP evidence. |

A lease can renew the same address indefinitely. An observed four-hour duration
is not a promise that the address will change every four hours. A static IPv4
profile instead configures the address on the host without DHCP assignment;
it still needs working Ethernet and, for external traffic, a working gateway.
It does not automatically make IPv6, DNS, or public service routing static.

## 6. What to expect during normal operation

- With the same physical MAC and an intact reservation, expect DHCP to continue
  assigning `.66`, including across ordinary renewals and reboots. This is an
  expectation supported by finite tests, not a permanent guarantee.
- During a deliberate reconnect or service restart, brief SSH/application
  interruption is expected. A terminal reconnects by starting a new SSH session;
  an existing TCP session need not survive an interruption.
- During boot, SSH can become reachable before PeerTube finishes starting. The
  suite allows a bounded settling period. A PeerTube-only failure does not by
  itself mean IP assignment failed.
- The friendly SSH name is local Mac configuration. Changing the underlying IP
  still requires updating its `HostName`; this alias does not create LAN DNS or mDNS.
- A stopped watchdog is normal after a verified non-reboot case. No transient
  watchdog can be relied on after a reboot.

The reported shell runtime includes scheduling, authentication, observation,
application startup, and cleanup. It is **not** a measured network outage or a
service-level objective. No MTTR, availability percentage, or failure probability
has been established.

## 7. Keeping the present system stable

1. Keep the existing eero reservation and active physical port/MAC aligned. Do
   not move the cable, randomize the MAC, duplicate `.66`, or change the subnet
   without reviewing the reservation and management path together.
2. Keep the tested DHCP profile in place while it meets the requirement. Make
   one change at a time and preserve known-good system/profile identities.
3. Use `ssh zimaboard`; keep host-key verification enabled. A host-key warning
   needs identity verification, not an automatic known_hosts deletion or disabled
   checking. Keep login keys and passphrases out of reports and repositories.
4. Keep software faults, router behavior, physical link problems, application
   readiness, and harness errors distinguishable. Diagnose the failed layer
   before adding a static profile or changing router configuration.
5. Run one controller at a time. After a failure, inspect the named action and
   watchdog before another fault or reboot. A human acknowledgement flag does
   not make the physical console reachable or prove the router settings.
6. Use maintenance windows for disruptive tests. Do not weaken sudoers or grant
   unrestricted passwordless `systemd-run` merely to remove prompts.
7. Save new reports under unique run-specific names outside Git, with reviewed
   evidence summaries filed intentionally. The current CLI can overwrite an old
   report when the same path is reused.

### Important source/deployment distinction

At the time of the 2026-11-03 snapshot, the local working tree/index contained
both the harness and an **imported, enabled static NetworkManager draft**. It had
not been activated on the board, but a switch from that draft could have applied
it. After the snapshot, the owner chose to keep DHCP as the default and make the
static draft explicitly opt-in. Consult the current `networking.nix` and its
tests for the default state; this historical finding is not deployment approval.

## 8. Failure triage: what could go wrong

| Symptom | Possible explanations | First safe response / next condition |
| --- | --- | --- |
| Old `.75` destination fails, `.66` works | Intended DHCP address transition; stale command/alias. | Use the stable SSH name and verify the same board identity, current address, and `auto` method. Not inherently a router fault. |
| `.66` absent or another address appears | Wrong port/MAC, reservation lost/changed, another DHCP server, changed client identity, or genuine server behavior. | Use the console and inspect interface/MAC, method, DHCP server/options, and eero's reservation/client list. Capture evidence before modifying profiles. |
| `.66` present but SSH fails | sshd, firewall, key/identity, boot timing, or wrong device at the address. | Separate reachability from authentication; verify identity from the console. Do not bypass host-key checking. |
| SSH works but DNS/public access fails | Resolver/cache differences, missing forwarding, gateway/WAN or IPv6 issues. | Inspect route/resolver state and test each layer. A static IPv4 address alone is not a fix. |
| SSH works but PeerTube fails after boot | Docker/application startup, dependency, storage, or service configuration. | Inspect bounded service/application evidence; do not classify this automatically as DHCP failure. Do not expose app secrets in journals/reports. |
| Action reports no carrier, board recovers later | PHY negotiation or NetworkManager readiness race; actual cable fault is also possible. | Inspect carrier and action logs, respect the bounded wait, and keep failed-case status. Investigate hardware if it persists with adequate wait. |
| Test fails but board is healthy | Harness/environment, job observation, authorization, timeout, or cleanup defect. | Match run ID, exit status, job logs, and final health. Never cancel a needed recovery merely to make the report look successful. |
| Another host appears to use `.66` | Address collision or stale identity/neighbor information. | Verify eero/ARP/device identity with controlled diagnostics. Do not intentionally create a second claimant on the home LAN. |
| Board does not return after reboot | Boot, storage, power, networking, reservation, or services; no transient watchdog survives. | Use the confirmed local console. Repeated blind reboots are not a diagnosis or rollback strategy. |

## 9. Taking the work further: conditions and success criteria

The decision hinge is **which demonstrated failure or additional requirement is
being addressed**. Both DHCP and static modes still need unique addressing,
correct physical identity, working LAN/gateway/DNS, and service recovery.

| Next investigation / improvement | When justified | What to test or preserve |
| --- | --- | --- |
| Repeat the DHCP group under the final harness revision | Before claiming repeatability or using it routinely. | Reconnect/restart/link cases over multiple sequential cycles; retain per-run results. Count failures as well as retries. |
| Passive observations over several lease renewals | If concern is time-dependent drift rather than fault injection. | Record address/method/MAC/server/lease, host boot changes, and application readiness over a chosen interval. This monitoring is proposed, not installed. |
| Physical cable/port test | If software bounce is insufficient or real link problems occur. | Coordinate console access; unplug/replug the SAME port first. A different port is a separate identity/configuration experiment. |
| eero reboot / router recovery | If stability after router restart matters. | Schedule household downtime, verify reservation persistence and post-router LAN/WAN/application recovery. Static addressing cannot preserve Internet access through an unavailable gateway. |
| DHCP packet/event capture | If the same correct client repeatedly receives an unexpected address. | Correlate client identifier, server, request/offer/ack timing, and router configuration. Review/redact captures; do not commit raw household traffic. |
| Host-side static IPv4 | If a correct reservation reproducibly fails, or the host must retain a LAN address while DHCP service is unavailable but the needed network path still exists. | Confirm collision avoidance; review the candidate and recovery profile; test static and DHCP phases separately. Do not use static addressing to hide unidentified link, DNS, or application failures. |
| Exercise watchdog recovery itself | Before depending on watchdogs as a primary recovery mechanism. | Separate, explicitly authorized failure/rollback rehearsal with console access; verify the timer actually executes and restores connectivity. Cancelled timers are not evidence of this. |
| Static/DHCP and generation round trips | Only after reviewed static deployment and explicit need for that recovery path. | Verify both transitions and original-state restoration; generation tests affect the whole running system/Home Manager/services and do not establish boot persistence. |
| Isolated failure lab | For DHCP loss/delay, duplicate addressing, rogue-server, or competing-route research. | Use a VM/isolated network, not the home LAN. Define expected failures and recovery before injecting them. |
| Independent management path | If remote recovery must work when the production LAN fails. | Design console/serial or a deliberately isolated management network. A spare cable into the same LAN is not automatically independent management. This is future architecture, not installed capability. |
| Better test provenance/observability | Before statistical claims, long-term monitoring, or automation without close supervision. | Include source revision/hash, start/end UTC and monotonic durations, cycle counts, stage-labelled results, and bounded post-action facts. Preserve failures and report schema versions. |

For routine operation, a low-impact read-only check is preferable to repeated
fault injection:

```sh
python3 -m scripts.network_resilience \
  --host 192.168.4.66 --host-key-alias zimaboard
```

Disruptive commands and safeguards are in the
[operator guide](../network-resilience-tests.md). No additional live tests,
router changes, system switches, or monitoring deployments were run to write
this report.

## 10. Remaining uncertainties and proof boundaries

- Why the original reservation/address transition did not happen when expected;
  no packet trace or authoritative reservation-change timeline was recorded.
- Reservation persistence through an eero restart, client allocation exclusion,
  long-term renewal behavior, and simultaneous events have not been tested here.
- Autonomous link/NetworkManager recovery without injected profile activation,
  physical unplug/replug, alternate-port behavior, and hardware reliability remain open.
- Watchdog execution under failure, recovery after losing the management path,
  static-profile activation, static/DHCP round trips, and generation restoration
  have not passed live tests. Console availability was asserted, not exercised.
- Public DNS/TLS/forwarding, external PeerTube/video functionality, storage/data
  integrity, and IPv6 resilience are outside the successful health gates.
- No repeated complete-group evidence or statistically meaningful reliability
  estimate exists. An individual successful reboot is not proof of every future boot.
- The exact tested harness bytes were not saved in each report; fixes occurred
  between runs. Report-time source digests identify the current code, not every
  historical execution. Earlier failed JSON reports were overwritten by retries.
- Terminal-character corruption remains an unconfirmed, separately scoped theory.

## 11. Provenance, verification, and filing status

- Historical networking intent:
  `luketych/ZimaBoard2--_@559f9ed68d1de34bd6847fff7976d446c8e39361`.
- Current local branch base:
  `luketych/nixos@9911a233f671595287661af71def9116761a33c2`.
- The harness, static draft, tests, and operational documentation are local
  staged/uncommitted changes at report capture, not a published implementation
  commit. The README and test guide also have unstaged edits; the new report
  files are untracked. Source-file SHA-256 values are in the companion evidence
  summary.
- Latest isolated verification before this report:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`:
  **57 tests, 56 passed, one skipped**. The skipped test is the optional paired
  real-chezmoi check, not a skipped live network test.
- `nix flake check --no-build --no-write-lock-file` and diff whitespace checks
  passed. Evaluation is not a full build, live activation, or rollback test.
- The five reviewed source reports originally live under `/tmp`; the companion
  JSON preserves selected facts, artifact timestamps, and full-file SHA-256
  digests so this report does not depend solely on ephemeral paths.
- Raw credentials, private/public SSH key material, trust-store contents,
  environment dumps, Pi sessions, bulk journals, and packet captures are not
  included. Human acknowledgements are explicitly distinguished from observations.

This is a local repository filing. It has not itself been committed, pushed,
posted as a GitHub issue/comment, or used to approve a deployment. Future
experiments should add dated findings and evidence rather than overwrite this
snapshot's historical conclusions.
