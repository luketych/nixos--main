#!/usr/bin/env python3
"""Regression coverage for the declarative ZimaBoard NetworkManager profiles."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

REPO = Path(__file__).resolve().parents[1]
SELECT_EXPRESSION = '''
c: {
  enabled = c.networking.networkmanager.enable;
  staticLanEnabled = c.local.zimaboard.staticLan.enable;
  ensureProfilesService = c.systemd.services ? NetworkManager-ensure-profiles;
  profiles = c.networking.networkmanager.ensureProfiles.profiles;
  settings = c.networking.networkmanager.settings;
}
'''
OPT_IN_EXPRESSION = '''
extend: let
  c = (extend {
    modules = [ ({ ... }: { local.zimaboard.staticLan.enable = true; }) ];
  }).config;
in {
  enabled = c.networking.networkmanager.enable;
  staticLanEnabled = c.local.zimaboard.staticLan.enable;
  ensureProfilesService = c.systemd.services ? NetworkManager-ensure-profiles;
  profiles = c.networking.networkmanager.ensureProfiles.profiles;
  settings = c.networking.networkmanager.settings;
}
'''
LAN_MAC = "00:e0:4c:5b:96:3e"
UNUSED_MAC = "00:e0:4c:5b:96:3f"


class ZimaBoardNetworkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nix = shutil.which("nix")
        if not nix:
            raise unittest.SkipTest("nix is required to evaluate the ZimaBoard NixOS configuration")

        def evaluate(attribute, expression):
            result = subprocess.run(
                [
                    nix,
                    "eval",
                    "--json",
                    "--no-write-lock-file",
                    attribute,
                    "--apply",
                    expression,
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(f"nix eval failed:\n{result.stderr}")
            return json.loads(result.stdout)

        cls.default_config = evaluate(
            ".#nixosConfigurations.zimaboard.config", SELECT_EXPRESSION
        )
        cls.config = evaluate(
            ".#nixosConfigurations.zimaboard.extendModules", OPT_IN_EXPRESSION
        )

    def test_default_is_networkmanager_dhcp_baseline(self):
        config = self.default_config
        self.assertTrue(config["enabled"])
        self.assertIs(config["staticLanEnabled"], False)
        self.assertIs(config["ensureProfilesService"], False)
        self.assertEqual(config["profiles"], {})
        self.assertEqual(config["settings"], {})
        self.assertNotIn("main", config["settings"])
        self.assertNotIn("device-zimaboard-unused", config["settings"])

    def test_opt_in_enables_networkmanager_profiles(self):
        self.assertTrue(self.config["enabled"])
        self.assertIs(self.config["staticLanEnabled"], True)
        self.assertIs(self.config["ensureProfilesService"], True)
        profiles = self.config["profiles"]
        self.assertEqual(set(profiles), {"zimaboard-lan-static", "zimaboard-lan-dhcp"})

    def test_static_profile_is_explicit_and_tied_to_the_lan_mac(self):
        profile = self.config["profiles"]["zimaboard-lan-static"]
        connection = profile["connection"]
        self.assertEqual(connection["id"], "zimaboard-lan-static")
        self.assertEqual(connection["uuid"], "5ce26162-ef4f-52f1-95a0-3cd02791b91e")
        parsed_uuid = uuid.UUID(connection["uuid"])
        self.assertEqual(str(parsed_uuid), connection["uuid"])
        self.assertEqual(connection["type"], "ethernet")
        self.assertIs(connection["autoconnect"], True)
        self.assertEqual(connection["autoconnect-priority"], 100)
        self.assertNotIn("interface-name", connection)

        ethernet = profile["ethernet"]
        self.assertEqual(ethernet["mac-address"], LAN_MAC)
        self.assertEqual(ethernet["cloned-mac-address"], "permanent")
        self.assertNotIn("interface-name", ethernet)

        ipv4 = profile["ipv4"]
        self.assertEqual(ipv4["method"], "manual")
        self.assertEqual(ipv4["address1"], "192.168.4.66/22")
        self.assertEqual(ipv4["gateway"], "192.168.4.1")
        self.assertEqual(ipv4["dns"], "192.168.4.1;1.1.1.1;")
        self.assertIs(ipv4["ignore-auto-dns"], True)
        self.assertIs(ipv4["may-fail"], False)
        self.assertEqual(ipv4["dad-timeout"], 3000)
        self.assertEqual(profile["ipv6"]["method"], "auto")

    def test_dhcp_profile_is_manual_recovery_only(self):
        profile = self.config["profiles"]["zimaboard-lan-dhcp"]
        connection = profile["connection"]
        self.assertEqual(connection["id"], "zimaboard-lan-dhcp")
        self.assertEqual(connection["uuid"], "d63ad997-9ab2-55d0-bc18-015b25571456")
        parsed_uuid = uuid.UUID(connection["uuid"])
        self.assertEqual(str(parsed_uuid), connection["uuid"])
        self.assertNotEqual(parsed_uuid, uuid.UUID(
            self.config["profiles"]["zimaboard-lan-static"]["connection"]["uuid"]
        ))
        self.assertEqual(connection["type"], "ethernet")
        self.assertIs(connection["autoconnect"], False)
        self.assertNotIn("interface-name", connection)
        self.assertEqual(profile["ethernet"]["mac-address"], LAN_MAC)
        self.assertEqual(profile["ethernet"]["cloned-mac-address"], "permanent")
        self.assertNotIn("interface-name", profile["ethernet"])

        ipv4 = profile["ipv4"]
        self.assertEqual(ipv4["method"], "auto")
        for override in ("address", "address1", "addresses", "gateway", "dns", "dns-search"):
            self.assertNotIn(override, ipv4)
        self.assertEqual(profile["ipv6"]["method"], "auto")

    def test_networkmanager_settings_only_suppress_the_two_physical_ports(self):
        settings = self.config["settings"]
        self.assertEqual(
            settings["main"]["no-auto-default"],
            f"mac:{LAN_MAC},mac:{UNUSED_MAC}",
        )
        self.assertNotIn("*", settings["main"]["no-auto-default"])

        unused = settings["device-zimaboard-unused"]
        self.assertEqual(unused["match-device"], f"mac:{UNUSED_MAC}")
        self.assertIs(unused["managed"], False)
        self.assertNotIn(LAN_MAC, unused["match-device"])
        self.assertNotIn("docker", unused["match-device"].lower())
        self.assertNotIn("veth", unused["match-device"].lower())
