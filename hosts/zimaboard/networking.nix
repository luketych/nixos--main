{ config, lib, ... }:

let
  # Match the physical ports rather than eth1/enp2s0, which vary across installs.
  lanMac = "00:e0:4c:5b:96:3e";
  unusedMac = "00:e0:4c:5b:96:3f";
in
{
  options.local.zimaboard.staticLan.enable = lib.mkEnableOption
    "the ZimaBoard's static LAN NetworkManager configuration";

  config = lib.mkMerge [
    # Keep the existing NetworkManager DHCP baseline regardless of opt-in.
    { networking.networkmanager.enable = true; }

    (lib.mkIf config.local.zimaboard.staticLan.enable {
      networking.networkmanager = {
        settings = {
          # Prevent new implicit wired DHCP profiles; existing profiles are preserved.
          main.no-auto-default = "mac:${lanMac},mac:${unusedMac}";
          # The spare port must not acquire a competing LAN address/default route.
          device-zimaboard-unused = {
            match-device = "mac:${unusedMac}";
            managed = false;
          };
        };

        ensureProfiles.profiles = {
          zimaboard-lan-static = {
            connection = {
              id = "zimaboard-lan-static";
              uuid = "5ce26162-ef4f-52f1-95a0-3cd02791b91e";
              type = "ethernet";
              autoconnect = true;
              autoconnect-priority = 100;
            };
            ethernet = {
              mac-address = lanMac;
              cloned-mac-address = "permanent";
            };
            ipv4 = {
              method = "manual";
              address1 = "192.168.4.66/22";
              gateway = "192.168.4.1";
              dns = "192.168.4.1;1.1.1.1;";
              ignore-auto-dns = true;
              may-fail = false;
              # Refuse activation if another host answers the duplicate-address probe.
              dad-timeout = 3000;
            };
            ipv6.method = "auto";
          };

          # Explicit recovery option, never an automatic switch back to DHCP.
          zimaboard-lan-dhcp = {
            connection = {
              id = "zimaboard-lan-dhcp";
              uuid = "d63ad997-9ab2-55d0-bc18-015b25571456";
              type = "ethernet";
              autoconnect = false;
            };
            ethernet = {
              mac-address = lanMac;
              cloned-mac-address = "permanent";
            };
            ipv4.method = "auto";
            ipv6.method = "auto";
          };
        };
      };
    })
  ];
}
