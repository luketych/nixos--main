# Tunarr on the ZimaBoard

Tunarr is deployed from the upstream `chrisbenincasa/tunarr:latest` Docker image using NixOS's OCI container module. The service listens on TCP port 8000 and is reachable on the LAN at `http://<zimaboard-ip>:8000` (currently `http://192.168.4.66:8000`). Port 8000 is allowed through the host firewall.

Persistent application configuration/database is stored at `/var/lib/tunarr` on the root filesystem and mounted at `/config/tunarr` in the container. Local media files can be placed under `/shared/tunarr-media`; that directory is mounted read-only in the container at `/media`. Docker is enabled by the host configuration. NixOS manages container start/stop and restart across system activations.

## Deploy

From the repository root on the ZimaBoard:

```sh
sudo nixos-rebuild switch --flake .#zimaboard
```

Then verify:

```sh
systemctl status docker-tunarr
curl -I http://127.0.0.1:8000
```

The first startup pulls the image and creates persistent state. Tunarr's initial UI setup is performed in a browser. The upstream quick start and current options are documented at <https://github.com/chrisbenincasa/tunarr>.

## Media access

In Tunarr, add a local-file media source using the container path `/media` (not the host path). Copy your own video files into `/shared/tunarr-media` first. The mount is read-only, so Tunarr cannot alter the originals. Do not mount broad host paths or place Tunarr state in the Nix store. If publishing through Caddy, give it a dedicated hostname and proxy to `127.0.0.1:8000`; do not expose the UI publicly without deciding on access controls first.

The image uses the mutable `latest` tag, following the upstream README quick-start. Pin a release tag or digest if reproducible image upgrades are required.
