{ config, pkgs, ... }:

{
  imports = [ ./hardware-configuration.nix ];

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking.hostName = "nixos";
  networking.networkmanager.enable = true;
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  time.timeZone = "America/Vancouver";
  i18n.defaultLocale = "en_CA.UTF-8";

  services.xserver.enable = true;
  services.displayManager.sddm.enable = true;
  services.desktopManager.plasma6.enable = true;
  services.xserver.xkb = {
    layout = "us";
    variant = "";
  };

  services.printing.enable = true;
  services.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  # This board is intended to run as an always-on server.
  services.logind.settings.Login.IdleAction = "ignore";
  systemd.sleep.settings.Sleep = {
    AllowSuspend = "no";
    AllowHibernation = "no";
    AllowHybridSleep = "no";
    AllowSuspendThenHibernate = "no";
  };

  users.users.luke = {
    isNormalUser = true;
    description = "Luke Tych";
    extraGroups = [ "networkmanager" "wheel" ];
    packages = with pkgs; [
      kdePackages.kate
    ];
  };

  users.groups.sharedFiles = {
    gid = 1000;
    members = [ "luke" ];
  };

  programs.firefox.enable = true;

  virtualisation.docker.enable = true;
  environment.systemPackages = with pkgs; [
    docker-compose
    git
    vim
    wget
  ];

  services.openssh.enable = true;

  # Serve PeerTube's canonical HTTPS hostname and proxy to its local HTTP port.
  services.caddy = {
    enable = true;
    virtualHosts."peertube.ktrx.cards".extraConfig = ''
      reverse_proxy 127.0.0.1:9000
    '';
  };
  networking.firewall.allowedTCPPorts = [ 80 443 ];

  fileSystems."/shared" = {
    device = "/dev/disk/by-uuid/9c4e390e-decb-4aa6-a50f-f6d6c05f62cd";
    fsType = "btrfs";
    options = [ "subvolid=5" "nofail" ];
  };

  system.stateVersion = "26.05";
}
