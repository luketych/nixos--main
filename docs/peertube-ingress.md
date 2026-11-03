# PeerTube ingress: Caddy configuration and diagnosis

## Why `/etc/caddy/Caddyfile` can be missing without an error

The packaged Caddy systemd unit names `/etc/caddy/Caddyfile`, but **NixOS adds a
systemd drop-in that replaces that command**. Reading only the first page of
`systemctl cat caddy` can hide the override and suggest the wrong file is used.

For this configuration, the effective command is shown below for recognition;
**do not launch a second Caddy process**:

```text
caddy run --config /etc/caddy/caddy_config --adapter caddyfile
```

`--config` selects the file; `--adapter caddyfile` selects its **format**, not its
filename. The file does not have to be named `Caddyfile`. NixOS also overrides
`ExecReload` to use the same path and adapter.

### Follow the configuration, not the filename

| Layer | Where to look |
| --- | --- |
| Desired configuration / source of truth | [`services.caddy` in `hosts/zimaboard/configuration.nix`](../hosts/zimaboard/configuration.nix): public hostname and `reverse_proxy 127.0.0.1:9000`. |
| Generated config | `/etc/caddy/caddy_config` → `/etc/static/caddy/caddy_config` → a generated file in `/nix/store/`. |
| Packaged unit and NixOS override | `systemctl cat caddy --no-pager` shows the base unit **and** drop-ins; an empty `ExecStart=` resets the packaged command before its replacement. |
| Loaded systemd command | `systemctl show caddy -p ExecStart -p ExecReload -p DropInPaths` shows the effective settings, not just the base unit. |
| Running process | Inspect the command line of `MainPID` to confirm what actually started. |

Run these read-only commands **on the ZimaBoard**:

```sh
systemctl cat caddy --no-pager
systemctl show caddy --no-pager \
  -p ActiveState -p SubState -p FragmentPath -p DropInPaths \
  -p ExecStart -p ExecReload -p MainPID
ls -l /etc/caddy/
readlink -f /etc/caddy/caddy_config
pid=$(systemctl show caddy -p MainPID --value)
if [ "${pid:-0}" -gt 0 ]; then
  ps -p "$pid" -o pid=,user=,args=
fi
```

Unit files on disk, systemd's loaded settings, and a running process can diverge
after an unapplied change; compare all three rather than assuming they agree.
Paths can change with NixOS versions: inspect the active system instead of
copying a `/nix/store/` hash from an old report.

**Do not rename the generated file, create a workaround `Caddyfile`, edit the
store-backed unit/config, or restart Caddy just to resolve this apparent naming
mismatch.** Persistent changes belong in the Nix declaration and the normal
reviewed deployment workflow. Do not use the Caddy admin API for an ad hoc fix;
that bypasses the declarative source of truth and may be lost on restart.

## Trace the failing hop

```text
Client → Cloudflare → home WAN/router → TCP forward → board firewall → Caddy :443 → PeerTube :9000
```

A healthy LAN does not prove this entire public path works. Start with read-only
checks; change one layer only after identifying evidence for it.

### 1. Check the application and Caddy separately

On the **ZimaBoard**, check PeerTube HTTP, then Caddy HTTPS with the real hostname
and certificate verification. `--resolve` bypasses public DNS/Cloudflare while
preserving the hostname for SNI and certificate validation.

```sh
curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
  --output /dev/null --write-out 'PeerTube HTTP=%{http_code}\n' \
  http://127.0.0.1:9000/
curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
  --resolve peertube.ktrx.cards:443:127.0.0.1 \
  --output /dev/null --write-out 'Caddy HTTPS=%{http_code} TLS_verify=%{ssl_verify_result}\n' \
  https://peertube.ktrx.cards/
ss -lnt
```

A failure at `:9000` points toward the application path; successful application
HTTP but failed local HTTPS warrants checking the effective Caddy config,
listeners, and bounded logs (`journalctl -u caddy --since '-15 min' -n 80 --no-pager`).
Use authorized elevated access if log permissions require it, and redact any
sensitive details before sharing. Do not add `--insecure` to make TLS checks pass.

### 2. Compare LAN HTTPS with the public URL

On the **Mac or another LAN client** (verify the board's current address first):

```sh
LAN_IP=192.168.4.66
curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
  --resolve "peertube.ktrx.cards:443:${LAN_IP}" \
  --output /dev/null --write-out 'LAN HTTPS=%{http_code} TLS_verify=%{ssl_verify_result}\n' \
  https://peertube.ktrx.cards/
curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
  --output /dev/null --write-out 'Public HTTPS=%{http_code}\n' \
  https://peertube.ktrx.cards/
```

**LAN HTTPS 200 + public Cloudflare 523** means Caddy answered that LAN request,
while Cloudflare reported an unreachable origin. It does not establish which
hop failed, nor rule out source-specific firewall rules. An HTTP 200 is not a
video-playback or long-running-stream test.

### 3. Inspect ingress routing before changing Caddy

- In **eero → Settings → Network settings → Reservations & port forwarding**,
  inspect TCP **443 → board:443** and **80 → board:80**. Confirm the destination
  is the active reserved board/device (`.66` in this investigation), not a stale
  `.73`/`.75` target. Check for conflicts; do not blindly recreate forwards.
- Check Cloudflare's configured origin A/AAAA records and any origin-routing
  overrides against the current WAN configuration. Proxied public DNS returns
  Cloudflare addresses, not necessarily the origin. Matching outbound public
  IPv4 alone does not prove inbound forwarding or exclude upstream NAT.
- Test the public URL from a phone **with Wi-Fi off**; record the time and result.
  If settings agree but it still fails, correlate a controlled external request
  with bounded ingress evidence at the board. No observed packets suggests
  checking the upstream path only after verifying the capture interface/filter;
  arriving packets shift attention toward firewall, return routing, and Caddy.
- Do not disable TLS verification, broaden firewall access, expose PeerTube's
  backend port, or publish WAN addresses/account details as a diagnostic shortcut.

## Investigation breadcrumb (2026-11-03)

Read-only inspection found that the effective systemd command **and running
process** used `/etc/caddy/caddy_config --adapter caddyfile`; the missing
`/etc/caddy/Caddyfile` was not the fault. Local and LAN HTTPS checks returned 200
with certificate verification passing, while the public URL returned Cloudflare
523. These are dated observations, **not** a claim of current public availability
or a diagnosed cause. See the [public-ingress report](reports/2026-11-03-peertube-ingress.md)
for the evidence, uncertainty, and WAN comparison.

**Later resolution:** Luke reported that opening the port restored public access.
See [known issues and resolutions](known_issues.md) for the fix, its evidence
limits, and recurrence checks; the linked investigation report predates this fix.
