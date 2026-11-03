# ZimaBoard physical-link and eero-reboot follow-up

- **Report date:** 2026-11-03 UTC
- **Status:** follow-up snapshot; 24-hour SSH observation still running at capture
- **Prior report:** [LAN stability investigation](2026-11-03-lan-resilience.md)
- **Companion evidence:** [curated observations and hashes](2026-11-03-lan-physical-eero.evidence.json)

## Objective

Extend the earlier software link-bounce and board-reboot checks with a physical
cable disconnect and an operator-performed eero reboot, then sample fresh SSH
connections to look for a previously observed intermittent timeout.

**ELI5:** We unplugged the real network cable and restarted the router to see
whether the board could be reached again at its reserved address. We also made
20 short SSH connections to see whether failures happened frequently during that
brief window.

## Results

| Check | Evidence and result | Boundary |
| --- | --- | --- |
| Pre-cable baseline | Read-only smoke at **16:05:37 UTC**: `.66/22`, expected active MAC, IPv4 `auto`, DNS/services/local PeerTube checks passed. | A point-in-time health snapshot, not proof of a later disconnect. |
| Physical cable | Luke reported unplugging and reconnecting the cable to the **same port**. Read-only smoke at **16:09:18 UTC** found the board at `.66` via DHCP, with services and local PeerTube healthy. No harness connection-up, watchdog, or reboot was used to restore it. | The unplug/replug and its duration were operator-reported; we did not continuously measure the interruption, log an exact carrier transition, or independently observe the physical act. |
| Eero reboot | Luke reported rebooting eero. Read-only smoke at **19:36:13 UTC** found `.66` via DHCP, with services and local PeerTube healthy. No board profile activation or board reboot was injected as part of this check. | We did not independently verify gateway identity/reboot, observe continuous connectivity, time the recovery, or capture DHCP packet exchanges. |
| Short SSH sample | **20 independent strict-host-key SSH attempts** at roughly 2-second intervals from **19:36:23–19:37:07 UTC** all succeeded. Median elapsed time: **0.231 s**; maximum: **0.808 s**. | One client and a 44-second window. It cannot establish long-term failure frequency or explain the previous timeout. |

Times for the smoke records are report-file modification times, not exact fault
or recovery timestamps. The before/after smoke JSON files are byte-identical:
their matching hashes mean their reported facts match, **not** that they are the
same observation or that the network stayed continuously available between them.
The earlier one-off SSH timeout remains unexplained.

## What this adds

- The physical-cable test adds one instance of **unassisted observed recovery**:
  after the operator-reported same-port disconnect, the board was healthy at
  `.66` without our harness manually activating a profile. It does not measure
  exactly when connectivity recovered or prove every cable/port failure mode.
- The eero test shows healthy board and application state **after** the reported
  router reboot. It does not show whether the address persisted throughout, was
  reacquired, or whether the full Internet/public path recovered.
- Local PeerTube HTTP and service checks do not verify public DNS/TLS, eero port
  forwarding, video playback, or external reachability.
- IPv4 method was still `auto`: this tests the DHCP setup, not the optional,
  default-disabled static configuration. Neither test deliberately exercised
  the harness's recovery watchdog.

## Ongoing observation and next steps

A separate 24-hour monitor was started on the Mac at **19:39:27 UTC**. It plans
one fresh strict-host-key SSH attempt every five minutes and records local
sampling gaps separately from observed failures. It was **still running when
this follow-up was filed**; do not treat an interim count as a completed result.
The monitor is local runtime state, not installed on the ZimaBoard, and will not
survive a Mac restart/logout automatically.

After completion, review the monitor's final summary, gaps, and failure
categories. A failure of the Mac-to-board SSH path is not, by itself, proof of a
board, router, Wi-Fi, gateway, or sshd defect. Capture bounded diagnostic facts
before deciding whether longer observation or an isolated fault test is needed.

A subsequent [public-ingress observation](2026-11-03-peertube-ingress.md)
recorded Cloudflare HTTP 523 despite verified-TLS Caddy HTTP 200 on the LAN.
A genuinely external client check and the cause remain follow-ups. Review draft
PR #4 and keep the static option disabled unless a concrete need and a
separately reviewed cutover justify enabling it.

## Provenance and filing

The [evidence summary](2026-11-03-lan-physical-eero.evidence.json) preserves
selected non-secret fields, UTC artifact times, and SHA-256 digests for the
four local source reports. Original `/tmp` files may be removed by the OS; no
credentials, SSH key material, Pi sessions, packet captures, or response bodies
are included. These files are a follow-up to the prior dated report; they do
not overwrite or retroactively alter that earlier snapshot.
