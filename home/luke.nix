{ lib, pkgs, ... }:

let
  piPackage = "@earendil-works/pi-coding-agent";
  piVersion = "1.4.0";
  subagentsRevision = "37c375bf13e3ac0e034856a54a56e62e4ecc6f8a";
  piSubagents = pkgs.fetchzip {
    url = "https://github.com/edxeth/pi-subagents/archive/${subagentsRevision}.tar.gz";
    hash = "sha256-PXK1ntSUEAlkLfw39y8a1l5KDi1CTeHj96kkisQ9JK0=";
  };
  piVccVersion = "0.7.2";
  piVcc = pkgs.fetchzip {
    url = "https://registry.npmjs.org/@sting8k/pi-vcc/-/pi-vcc-${piVccVersion}.tgz";
    hash = "sha256-e80FNQgIYoQw6DQUxBrA3kPw1By+D8yZe9tQ+uhBNwY=";
  };
  chezmoiPi = pkgs.writeShellApplication {
    name = "chezmoi-pi";
    runtimeInputs = [ pkgs.chezmoi pkgs.git pkgs.jq ];
    text = builtins.readFile ./chezmoi-pi.sh;
  };
  piProfileLaunchers = pkgs.symlinkJoin {
    name = "pi-profile-launchers";
    paths = map (name: pkgs.writeShellScriptBin name ''
      exec "$HOME/.local/bin/${name}" "$@"
    '') [ "pi" "pi0" "pi1" ];
  };
in
{
  home.username = "luke";
  home.homeDirectory = "/home/luke";
  home.stateVersion = "26.05";

  home.packages = [
    pkgs.chezmoi
    pkgs.git
    pkgs.nodejs_24
    pkgs.python3
    chezmoiPi
    piProfileLaunchers
  ];

  home.sessionPath = [ "$HOME/.local/bin" ];

  programs.home-manager.enable = true;

  home.file = {
    ".local/share/pi-managed/pi-subagents".source = piSubagents;
    ".local/share/pi-managed/pi-vcc".source = piVcc;
    ".local/share/pi-managed/manifest.json".text = builtins.toJSON {
      schemaVersion = 1;
      minimumPiVersion = "0.85.0";
      cli = {
        package = piPackage;
        version = piVersion;
        entrypoint = "pi-cli/${piVersion}/bin/pi";
      };
      extensions = {
        pi-subagents = {
          version = "2.9.1";
          revision = subagentsRevision;
          entrypoint = "pi-subagents/src/index.ts";
        };
        pi-vcc = {
          version = piVccVersion;
          entrypoint = "pi-vcc/index.ts";
        };
      };
    };
  };

  home.activation.installPiCli = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    set -eu

    export NPM_CONFIG_UPDATE_NOTIFIER=false
    export PATH="${pkgs.git}/bin:${pkgs.nodejs_24}/bin:$PATH"
    export PI_PACKAGE="${piPackage}"
    export PI_VERSION="${piVersion}"
    export PI_NPM_BIN="${pkgs.nodejs_24}/bin/npm"
    export PI_NODE_BIN="${pkgs.nodejs_24}/bin/node"
    export PI_PREFIX="$HOME/.local"

    ${pkgs.bash}/bin/bash ${./install-pi-cli.sh}
  '';
}
