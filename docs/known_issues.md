# Known issues and resolutions

## PeerTube inaccessible through Cloudflare despite healthy local Caddy

**Status:** resolved — Luke reported that opening the port restored access to
<https://peertube.ktrx.cards/> following the 2026-11-03 ingress investigation.

### Symptoms and diagnostic clues

- The public URL returned **Cloudflare HTTP 523 (origin unreachable)**.
- PeerTube answered locally at `http://127.0.0.1:9000/` with HTTP 200.
- Caddy answered HTTPS requests using the public hostname over loopback and the
  LAN with HTTP 200 and certificate verification passing.
- The board's active LAN address was **`192.168.4.66` via DHCP reservation**.
- The outbound public IPv4 matched the Cloudflare origin address supplied by
  Luke. That match alone did not establish that inbound traffic could reach Caddy.

### Resolution: open the required inbound port

After the investigation turned to eero's port forwarding, Luke opened the port
and confirmed the site worked. This supports an inbound port-access problem,
not the apparent Caddy configuration filename mismatch described below.

The exact port number, changed rule, and previous rule state were not recorded;
this entry does **not** assert that both ports below were changed or that the
router configuration was independently audited. Recovery is operator-confirmed,
not a new automated external-client test or a full video-playback test.

For this deployment, the documented router forwarding setup is:

| Protocol | WAN port | LAN destination at investigation time |
| --- | --- | --- |
| TCP | 443 (HTTPS) | `192.168.4.66:443` — Caddy |
| TCP | 80 (HTTP / automatic HTTPS support) | `192.168.4.66:80` — Caddy |

Verify the board's current reserved address/device before applying these values.
A DHCP reservation assigns a stable LAN address; it does **not** create a port
forward. Likewise, `networking.firewall.allowedTCPPorts = [ 80 443 ];` opens the
NixOS host firewall, **not** eero's WAN path. NixOS does not manage these router
forwards. Do not expose PeerTube's backend port 9000 as a workaround.

### Misleading clue: `Caddyfile` versus `caddy_config`

The packaged systemd unit refers to `/etc/caddy/Caddyfile`, but NixOS supplies a
drop-in that replaces the start/reload commands. During the investigation, both
the effective start command and running process used:

```text
caddy run --config /etc/caddy/caddy_config --adapter caddyfile
```

`--adapter caddyfile` specifies the format, not a required filename. The missing
`/etc/caddy/Caddyfile` was therefore not the problem. Do not rename the generated
file or create a replacement to match the base unit. Inspect the full unit and
its effective settings on the board instead:

```sh
systemctl cat caddy --no-pager
systemctl show caddy --no-pager -p ExecStart -p ExecReload -p DropInPaths
```

Persistent Caddy changes belong in
[`hosts/zimaboard/configuration.nix`](../hosts/zimaboard/configuration.nix), not
the generated `/etc/caddy/` files or `/nix/store/` paths.

### If this happens again

1. Compare PeerTube HTTP, direct LAN Caddy HTTPS, and public HTTPS separately.
   A LAN HTTP 200 does not establish Internet reachability; a Cloudflare 523
   alone does not identify the failing hop.
2. Inspect eero's **Reservations & port forwarding** settings. Confirm the
   required TCP forwards target the active board/device, not an old `.73` or
   `.75` address, and check for conflicting entries before changing anything.
3. Verify Cloudflare's configured origin A/AAAA records against the current WAN
   configuration. Proxied DNS answers show Cloudflare addresses, not necessarily
   the origin. If forwarding is correct, investigate upstream NAT, host firewall
   rules, and whether the external request reaches the board.
4. Retest the public URL from a phone with **Wi-Fi disabled** or another external
   network. Record the exact rule change and result; verify playback separately.
   Do not disable TLS verification or broadly open additional ports.

See the [Caddy/ingress troubleshooting guide](peertube-ingress.md) for bounded,
read-only diagnostic commands. The [original ingress report](reports/2026-11-03-peertube-ingress.md)
preserves the pre-fix observations and uncertainty; its open-investigation status
predates the operator-reported resolution recorded here.
