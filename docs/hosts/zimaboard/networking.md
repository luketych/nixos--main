# ZimaBoard static LAN address

## Default-disabled draft

The static LAN configuration is **disabled by default**. NetworkManager remains
enabled with the existing DHCP baseline; with no opt-in there are no declarative
static or recovery profiles, no `no-auto-default` override, and no spare-port
unmanaged rule. No deployment, live network change, or system rebuild has
occurred.

Enabling it requires an explicit source change:

```nix
local.zimaboard.staticLan.enable = true;
```

Do not enable this option until the address/reservation and console safeguards
below are reviewed. Evaluate the default and opted-in configurations, run the
focused tests, and review a full system-generation build/diff before any
separately authorized activation. A passing evaluation or build is not a
network test and does not authorize deployment.

## Intended configuration when explicitly enabled

`hosts/zimaboard/networking.nix` recreates the LAN settings recorded in
[`ZimaBoard2--_`](https://github.com/luketych/ZimaBoard2--_/blob/559f9ed68d1de34bd6847fff7976d446c8e39361/configs/networking/lan-ip.conf)
using NixOS's native NetworkManager profile support, not the old shell helper.

| Setting | Value |
| --- | --- |
| Active Ethernet MAC | `00:e0:4c:5b:96:3e` (currently `enp2s0`) |
| Static IPv4 address | `192.168.4.66/22` |
| Gateway | `192.168.4.1` |
| IPv4 DNS | `192.168.4.1`, `1.1.1.1` |
| Spare Ethernet MAC | `00:e0:4c:5b:96:3f` (currently `enp1s0`) |

The `zimaboard-lan-static` profile matches the physical MAC rather than an
interface name, preserves the permanent MAC, and has autoconnect priority 100.
IPv4 duplicate-address detection must succeed before activation. IPv6 remains
in automatic mode; its routes and DNS are not disabled by the IPv4 settings.

When enabled, `zimaboard-lan-dhcp` is an explicitly activated recovery profile
for the same MAC, with autoconnect disabled. The spare port is then unmanaged
so it cannot acquire a competing LAN route. Docker bridges and veth interfaces
are not matched by this rule.

When the option is enabled, NetworkManager will not generate new default wired
profiles for these two MACs. **Existing profiles are not deleted or disabled by
that setting.** In particular,
`Wired connection 2` may remain active after profiles are loaded: a higher
priority does not preempt an already active connection. Explicitly activate the
static profile during the reviewed cutover. Legacy DHCP profiles can still be
selected if static activation fails; retire their autoconnect only after the
static setup is verified, leaving the profiles available for manual recovery.

## Separate resilience tests

Before this static configuration is activated, use the
[opt-in network resilience suite](network-resilience-tests.md) to test eero's
reservation while the board is still on DHCP. After a verified cutover, its
static mode can check manual addressing, spare-port isolation, runtime DHCP
recovery, and a separately authorized previous-generation round trip. These
live cases are never run by ordinary unit tests and have not been validated by
merely passing Nix evaluation.

## Before activation

- In eero, confirm `00:e0:4c:5b:96:3e -> 192.168.4.66` is reserved. Keep the
  reservation even with a static address, so DHCP cannot allocate `.66` to
  another device. NixOS does not configure eero or verify its reservation.
- Check the eero client/lease list for conflicts. An unanswered ping alone
  does not prove the address is unused. On the board, a supplementary probe is
  `sudo arping -D -I enp2s0 -c 3 -w 5 192.168.4.66`; confirm the interface first.
- Ensure the Ethernet cable is in the port with MAC `00:e0:4c:5b:96:3e`.
- Arrange local console access before the first cutover. A plain remote
  `nixos-rebuild switch` can disconnect SSH before verification or recovery.
  SSH-only activation needs a separately prepared, detached timed rollback
  that restores both the previous NixOS generation and DHCP connection.
- Exit Pi and other full-screen terminal programs before an intentional SSH
  disconnect. This avoids leaving the local terminal in an application mode.
- Review the complete system-generation difference: a NixOS activation also
  activates the integrated Home Manager profile. A standalone Home Manager
  switch does **not** install these host networking settings.

## Validate and build without changing the live network

From a reviewed checkout, with intended new files tracked/staged for Git-backed
flake evaluation:

```sh
nix flake check --no-build --no-write-lock-file
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_zimaboard_networking -v
```

Only after explicitly setting `local.zimaboard.staticLan.enable = true;` in
the source, reviewing the test/build results, and completing the safeguards
above, build the full generation on the ZimaBoard before any separately
approved cutover:

```sh
cd /home/luke/nixos
nix build --no-write-lock-file .#nixosConfigurations.zimaboard.config.system.build.toplevel
new_system=$(readlink -f result)
old_system=$(readlink -f /run/current-system)
nix store diff-closures "$old_system" "$new_system"
```

Save the full `old_system` path and the active DHCP connection UUID somewhere
available at the console. For the currently connected interface:

```sh
nmcli -g GENERAL.CON-UUID device show enp2s0
```

## Console-assisted first activation

These commands change connectivity. Run them at the local console after the
reservation/conflict checks above, not as an unattended SSH deployment.

1. Test the built generation without making it the boot default:

   ```sh
   sudo "$new_system/bin/switch-to-configuration" test
   sudo nmcli connection up zimaboard-lan-static
   ```

   NixOS may restart NetworkManager while applying its configuration. Profile
   generation itself reloads connections but does not guarantee a live switch.

2. Verify `192.168.4.66/22`, the default route via `.1`, DNS resolution, and services:

   ```sh
   nmcli -f GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show enp2s0
   ip -4 route show default
   ping -c 2 192.168.4.1
   getent ahostsv4 github.com
   systemctl is-active NetworkManager sshd caddy
   ```

   From the Mac, reconnect with `ssh luke@192.168.4.66` and verify the host-key
   fingerprint against the existing board identity if asked to trust the new IP.
   Do not disable SSH host-key checking.

3. If the test fails, restore the saved system generation and previous DHCP
   connection from the console:

   ```sh
   sudo "$old_system/bin/switch-to-configuration" test
   # A removed declarative profile can remain in /run until reboot. For this
   # first migration, remove the new static profile before restoring DHCP.
   sudo nmcli connection delete uuid 5ce26162-ef4f-52f1-95a0-3cd02791b91e
   sudo nmcli connection up uuid <saved-DHCP-connection-UUID>
   ```

   Do not assume eero will return the same DHCP address. A reboot also clears
   the generated runtime profiles and uses the previous boot configuration,
   provided the new generation has only been tested, not switched persistently.

4. After successful verification, disable autoconnect on the confirmed legacy
   wired DHCP profile(s) for the active port if present. Do not delete them or
   change Docker profiles. For the previously verified active profile:

   ```sh
   sudo nmcli connection modify uuid <saved-DHCP-connection-UUID> connection.autoconnect no
   sudo nixos-rebuild switch --flake /home/luke/nixos#zimaboard
   ```

   Retiring an old, locally created profile is a one-time migration step; Nix
   owns the new static/recovery profiles. Do not edit those declarative profiles
   with `nmcli connection modify`, which can create conflicting persistent copies.

5. Reboot when convenient and verify the address/profile and services again.
   Point eero's TCP 80/443 forwards at `.66` and test PeerTube from outside the LAN.

For temporary recovery while this generation is active, the console can use
`sudo nmcli connection up zimaboard-lan-dhcp`. This does not change the desired
static configuration or its next-boot preference.
