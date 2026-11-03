# PeerTube public ingress observation

- **Date:** 2026-11-03 UTC
- **Status:** investigation open; cause not yet established
- **Related:** [LAN physical-link/eero follow-up](2026-11-03-lan-physical-eero.md)
- **Companion evidence:** [curated check results](2026-11-03-peertube-ingress.evidence.json)

## Objective

Check whether the LAN-side PeerTube health established during the network
resilience tests also implies a working public access path.

**ELI5:** PeerTube answers when we talk directly to the ZimaBoard, but its
public web address travels through Cloudflare first. During these checks,
Cloudflare reported that it could not reach the destination behind it.

## Observations

The following were read-only checks on 2026-11-03; no router, Cloudflare, NixOS,
Caddy, PeerTube, or firewall configuration was changed:

| Path | Result | Interpretation |
| --- | --- | --- |
| ZimaBoard → `127.0.0.1:9000` | HTTP 200 | The local PeerTube HTTP endpoint answered at check time. |
| ZimaBoard → Caddy loopback using the public hostname | HTTPS 200, certificate verification passed | Caddy served this hostname locally at check time. This was not Cloudflare's connection to the origin. |
| Mac → `192.168.4.66:443` directly, with the public hostname via `curl --resolve` | HTTPS 200, certificate verification passed | The Mac reached the board's Caddy endpoint over the LAN without Cloudflare. |
| Mac → public `https://peertube.ktrx.cards/` via Cloudflare | HTTP **523**, `Server: cloudflare`; public-facing TLS certificate verification passed | A Cloudflare edge returned an origin-unreachable error. This is not a successful public PeerTube response, and it does not identify the misconfigured or failing hop. |
| Mac → public URL in a later attempt | Connection timeout | A separate connection attempt did not complete; this does not by itself identify a cause. |

DNS resolved the public name to Cloudflare proxy addresses. A Cloudflare error
received by a home-network client is evidence about that edge response, but the
client itself was **not independently outside the home network**. An attempted
separate third-party reader probe could not connect; Google PageSpeed returned
HTTP 429. Neither was a usable independent external-client confirmation.

## What remains uncertain

- Whether the Cloudflare DNS/origin configuration targets the correct current
  public address and address family. Proxied public DNS answers expose
  Cloudflare addresses, not necessarily the configured origin.
- Whether eero's TCP 80/443 forwards target `192.168.4.66`, whether an upstream
  router/NAT or ISP connection affects inbound routing, or whether Cloudflare
  could reach the origin at other times.
- Whether the public client timeout and earlier intermittent SSH timeout share
  a cause. They involve different paths; no causal link is established.
- External video playback, authentication, uploads, and long-running traffic.

An HTTP 200 from Caddy on the LAN does not rule out an incorrect router forward
or another upstream issue. The certificate verified in the local test is also
not proof that Cloudflare can reach or negotiate TLS with that origin.

### Later read-only WAN comparison

At approximately **20:14 UTC**, Luke supplied the IPv4 address shown as the
Cloudflare origin. Independently, two public-IP echo endpoints queried from both
the Mac and the ZimaBoard returned an IPv4 address that **matched the address
Luke supplied**. Only the comparison was printed; the WAN address is not included
in this report. This reduces the likelihood of a stale origin IPv4 address at
that time but does not independently inspect the Cloudflare DNS record or prove
that inbound connections reach eero, pass a forward/firewall, or reach Caddy.
The public URL continued to return HTTP 523; LAN Caddy HTTPS remained 200 with
certificate verification passing. No forwarded-port configuration was inspected
or changed.

## Recommended next checks (no automatic changes)

1. From a phone **with Wi-Fi disabled** or another independently connected
   network, open `https://peertube.ktrx.cards/`; record status and time. Do not
   enter credentials during this diagnostic if the identity/certificate is
   unexpected. This distinguishes a genuinely external client check from our
   home-network test, but a Cloudflare error can still have multiple causes.
2. In eero, inspect—do not blindly recreate—the existing TCP **80 and 443**
   port forwards and confirm they target the reserved `.66` device/MAC. Note any
   duplicate entries, stale `.73`/`.75` target, or upstream NAT arrangement.
3. In the Cloudflare dashboard, inspect the origin A/AAAA records and relevant
   routing/proxy settings against the current eero/ISP WAN addresses. Treat WAN
   addresses and dashboard/account details as sensitive operational data; don't
   commit them or post credentials/tokens/screenshots containing secrets.
4. If those settings agree and the error persists, review bounded relevant
   Cloudflare/eero/Caddy connection evidence and ISP reachability; avoid changing
   multiple layers at once. Do not disable TLS verification or expose additional
   ports to troubleshoot.

The scheduled SSH monitor remains a separate, single-Mac-to-board LAN check; it
does not test this public path or prove public service availability.
