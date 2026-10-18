{
  description = "NixOS configuration for the ZimaBoard";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs, ... }: {
    nixosConfigurations.zimaboard = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [ ./hosts/zimaboard/configuration.nix ];
    };
  };
}
