# netboot.xyz on Athena

`netboot.xyz` runs in DSM Container Manager on the Synology NAS (`Athena`) and
serves PXE clients on Lab Infra VLAN 20. UniFi remains the only DHCP server;
this project provides TFTP, the iPXE menu, and HTTP-hosted boot assets.

This is deliberately an explicit-apply Compose project. The future Git-driven
Synology deployment plane is tracked in
[home-lab#379](https://github.com/kelchm/home-lab/issues/379).

## Endpoints

| Endpoint | Use |
|---|---|
| `10.32.20.5:69/udp` | TFTP bootloader and menu files |
| `http://10.32.20.5:3000/` | netboot.xyz administration UI; restrict to trusted management clients |
| `http://10.32.20.5:8080/` | Local boot assets |

Runtime state is adjacent to the deployed Compose file:

```text
/volume1/docker/netbootxyz/
├── compose.yaml       # copied from this directory
├── config/            # generated menus and web-app state
│   └── menus/
│       ├── local-vars.ipxe    # copied from this directory
│       ├── proxmox.ipxe       # copied from this directory
│       └── systemrescue.ipxe  # copied from this directory
└── assets/            # downloaded installers and live-image assets
```

`config/` is small and worth preserving before an upgrade. `assets/` is a
reconstructable cache and should not be committed to Git.

## Deploy or update

Validate locally first:

```sh
docker compose -f synology/netbootxyz/compose.yaml config --quiet
```

Copy the authoritative file to the NAS and apply it. Athena's SSH file-transfer
subsystem rejects `scp`, so use `rsync`; create the bind-mount targets before the
first start:

On the first PVE `9.2-1` deployment, complete the asset-staging procedure below before copying `proxmox.ipxe`. Do not publish a boot selection whose kernel, initrd, or ISO path is still absent.

```sh
ssh kelchm@10.32.20.5 \
  'mkdir -p /volume1/docker/netbootxyz/config /volume1/docker/netbootxyz/assets'
rsync -av synology/netbootxyz/compose.yaml \
  kelchm@10.32.20.5:/volume1/docker/netbootxyz/compose.yaml
rsync -av synology/netbootxyz/local-vars.ipxe \
  kelchm@10.32.20.5:/volume1/docker/netbootxyz/config/menus/local-vars.ipxe
rsync -av synology/netbootxyz/proxmox.ipxe \
  kelchm@10.32.20.5:/volume1/docker/netbootxyz/config/menus/proxmox.ipxe
rsync -av synology/netbootxyz/systemrescue.ipxe \
  kelchm@10.32.20.5:/volume1/docker/netbootxyz/config/menus/systemrescue.ipxe
ssh kelchm@10.32.20.5 '
  sudo chown 1000:1000 \
    /volume1/docker/netbootxyz/config/menus/local-vars.ipxe \
    /volume1/docker/netbootxyz/config/menus/proxmox.ipxe \
    /volume1/docker/netbootxyz/config/menus/systemrescue.ipxe
  sudo chmod 0755 \
    /volume1/docker/netbootxyz/config/menus/local-vars.ipxe \
    /volume1/docker/netbootxyz/config/menus/proxmox.ipxe \
    /volume1/docker/netbootxyz/config/menus/systemrescue.ipxe
  cd /volume1/docker/netbootxyz
  sudo /usr/local/bin/docker-compose pull
  sudo /usr/local/bin/docker-compose up -d
  sudo /usr/local/bin/docker-compose ps
'
```

The equivalent DSM path is **Container Manager → Project** with project name
`netbootxyz-nas`, path `/volume1/docker/netbootxyz`, and the committed YAML as
the project definition. DSM is an apply/inspection surface, not a second source
of truth.

The image and menu release are pinned independently. Renovate can update the
image tag/digest in the Compose file, but Synology changes must not be merged or
applied without reviewing upstream changes and the persistent-config format.

## Local boot assets

For PVE commissioning, follow the [PVE node bootstrap runbook](../../docs/runbooks/pve-node-bootstrap.md). The pinned menu release `3.0.2` advertises PVE `9.1-1`, so the committed `proxmox.ipxe` preserves the generated PBS, PDM, and PMG choices while replacing only PVE with an explicit local `9.2-1` entry. Do not point the deployment at netboot.xyz's development menu and do not select the older entry after a menu refresh.

The custom entry uses netboot.xyz asset-mirror release [`9.2-1-4bbcc809`](https://github.com/netbootxyz/asset-mirror/releases/tag/9.2-1-4bbcc809). Stage its three x86-64 assets directly on Athena, verify them before publication, and leave an existing published set untouched:

```sh
ssh kelchm@10.32.20.5 '
  set -eu
  asset_parent=/volume1/docker/netbootxyz/assets/asset-mirror/releases/download
  asset_dir="$asset_parent/9.2-1-4bbcc809"
  staging_dir="$asset_dir.staging"
  sudo mkdir -p "$asset_parent"
  sudo test ! -e "$asset_dir"
  sudo mkdir -p "$staging_dir"
  cd "$staging_dir"
  sudo curl -fL --retry 3 \
    -o initrd \
    https://github.com/netbootxyz/asset-mirror/releases/download/9.2-1-4bbcc809/initrd
  sudo curl -fL --retry 3 \
    -o proxmox.iso \
    https://github.com/netbootxyz/asset-mirror/releases/download/9.2-1-4bbcc809/proxmox.iso
  sudo curl -fL --retry 3 \
    -o vmlinuz \
    https://github.com/netbootxyz/asset-mirror/releases/download/9.2-1-4bbcc809/vmlinuz
  printf "%s  %s\n" \
    8fdf76b44287af130358c142f1743c30e1b10df21bae1693283736603bef3cba initrd \
    4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c proxmox.iso \
    9eaf9a7fa2cc55815863db9af76d3de20cb491ca77839febd198d392d4c2e171 vmlinuz \
    | sudo sha256sum -c -
  sudo chmod 0644 initrd proxmox.iso vmlinuz
  cd "$asset_parent"
  sudo mv "$staging_dir" "$asset_dir"
'
```

The `proxmox.iso` digest above is the [official Proxmox VE 9.2-1 x86-64 SHA-256](https://www.proxmox.com/en/downloads/proxmox-virtual-environment). The `initrd` and `vmlinuz` digests are the immutable asset digests published with the pinned netboot.xyz asset-mirror release.

Runtime status as of 2026-08-26: Athena holds the complete `9.2-1-4bbcc809` directory, all three on-disk hashes pass, and nginx returns HTTP 200 with the expected content lengths. The generated PVE 9.1-1 submenu remains active until this repository change is merged and explicitly applied; the PVE 9.2-1 cold-boot acceptance test is still pending.

Use the administration UI's **Local Assets** page to download the files needed
by a menu entry. For SystemRescue 13.00, select the four
`systemrescue-amd64` files under `13.00-d20a63ac`: `airootfs.sfs`, `initrd`,
`vmlinuz`, and `archiso_pxe_http`.

The qualification-only SystemRescue 12.03 entry uses the same four files under
`12.03-d20a63ac`. Their GitHub release-asset SHA-256 values are:

```text
a9f3a16e266d11f660f2be4e03463da094c885f55d9faa5202c63276b4b9d6f9  airootfs.sfs
58f59c666b892708a08d79b203e3f4a99c3d55fd74f7ecddf335a9173aedc13c  archiso_pxe_http
7a1a5140833d55309d5537865b5aed54eb01ea3a6eadb4a01e76a1026391630a  initrd
fdce1c57439c2fc6e04549940f6e67cd38a8751888f5dad37d42db613a5e931d  vmlinuz
```

SystemRescue 12.03 boots Linux 6.12.61, before the Linux 6.13 NVMe HMB
single-segment allocator change. Its menu entry adds `copytoram=y` and exists
only for read-only allocator discovery. The extracted netboot.xyz release does
not include SystemRescue's optional `airootfs.sha512`, so do not add
`checksum=1`; verify the four cached assets against the release SHA-256 values
above before booting instead. Stock SystemRescue does not include OpenZFS; do
not mistake this boot for the later ZFS workload environment.

The committed `local-vars.ipxe` sets `live_endpoint` to Athena's HTTP endpoint.
The netboot.xyz bootloader requests this override before the generated menus,
whose asset paths are then resolved beneath `http://10.32.20.5:8080`. Copy the
override and the committed `proxmox.ipxe` and `systemrescue.ipxe` files after initial menu generation, after recreating `config/`, and after upstream menu refreshes. The downloaded files in `assets/` remain runtime cache rather than Git-managed content.

## Verification

From another VLAN 20 host:

```sh
tftp 10.32.20.5 -g -r netboot.xyz-snponly.efi
curl -fsSI http://10.32.20.5:8080/
curl -fsSI http://10.32.20.5:8080/asset-mirror/releases/download/9.2-1-4bbcc809/vmlinuz
curl -fsSI http://10.32.20.5:8080/asset-mirror/releases/download/9.2-1-4bbcc809/initrd
curl -fsSI http://10.32.20.5:8080/asset-mirror/releases/download/9.2-1-4bbcc809/proxmox.iso
```

Also verify a container restart does not lose menus or assets:

```sh
ssh kelchm@10.32.20.5 '
  cd /volume1/docker/netbootxyz
  sudo /usr/local/bin/docker-compose restart
  sudo /usr/local/bin/docker-compose ps
'
```

## UniFi network-boot setting

On the VLAN 20 network, set Network Boot to:

- TFTP / next-server: `10.32.20.5`
- boot filename: `netboot.xyz-snponly.efi`

Cutover completed on 2026-08-18. Two SystemRescue boots reached a usable console
and SSH session; the second served the full image locally at about 110 MB/s.

## Rollback

The former GLKVM endpoint no longer exists and is not a rollback target. For a
bad container update, restore the prior pinned Compose file with `git revert`,
copy it to the NAS, and run
`sudo /usr/local/bin/docker-compose up -d` again.

The container is not in the data path after Linux finishes booting. A complete
NAS or Container Manager outage can always be bypassed with a physical
SystemRescue installer USB.

## Completed GLKVM cleanup

After two successful boots on 2026-08-18, the Python process was stopped and
these temporary paths were removed:

```text
/userdata/media/netboot
/userdata/media/sysresccd
/userdata/media/boot.ipxe
```

The GLKVM HID and console helpers were preserved, moved from `/tmp` to the
persistent user partition, and captured with recovery instructions under
[`devices/glkvm/`](../../devices/glkvm/).
