{ config, lib, pkgs, ... }:
let
  # A pure evaluation (including flake check) always uses the KDE-only default.
  requested = builtins.getEnv "NIXOS_CLI_WINDOW_MANAGER";
  manager = config.local.zimaboard.windowManager;
  cli = pkgs.writeShellScriptBin "nixos_cli" ''
    exec ${pkgs.python3}/bin/python3 ${../../scripts/nixos_cli.py} "$@"
  '';
in
{
  options.local.zimaboard.windowManager = lib.mkOption {
    type = lib.types.enum [ "none" "sway" "niri" "hyprland" ];
    default = if requested == "" then "none" else requested;
    description = "Optional tiling session alongside KDE Plasma.";
  };

  config = {
    programs.sway.enable = manager == "sway";
    programs.niri.enable = manager == "niri";
    programs.hyprland.enable = manager == "hyprland";

    environment.systemPackages = [ cli ]
      ++ lib.optional (manager == "niri") pkgs.alacritty
      ++ lib.optional (manager == "hyprland") pkgs.kitty;

    environment.etc."nixos-cli/composition.json".text = builtins.toJSON {
      schema = 1;
      windowManager = manager;
      revision = config.system.configurationRevision;
    };
  };
}
