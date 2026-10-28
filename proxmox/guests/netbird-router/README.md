# NetBird routing peer

VM 103 is NetBird's only routing peer. It carries traffic from away clients to the household HTTPS, admin HTTPS and DNS resources. NetBird Cloud objects, client ownership, access verification and recovery limits are in the [NetBird runbook](../../../docs/runbooks/netbird-remote-access.md); setup and qualification are tracked in [#625](https://github.com/kelchm/home-lab/issues/625).

Read back on 2026-10-27:

| Setting | Value |
|---|---|
| VMID and name | `103`, `netbird-pilot-router`. Dropping `pilot` from names is an open #625 decision. |
| Node | `pve-sbx-1`. Template 9000 is on that node's `local-lvm`, so a rebuild clones there. |
| Network | `10.32.19.103/24` on VLAN 19 (`vmbr0` tag 19); gateway and resolver `10.32.19.1`; search domain `home.kelch.io` |
| Guest | Full clone of template 9000 (Debian 13 genericcloud): 1 vCPU, 1 GiB RAM, 8 GiB `local-lvm`, OVMF, QEMU guest agent |
| Boot | `onboot: 1`, `startup: order=10,up=30` |
| Protection, backup, HA | No `protection`. Excluded from `daily-backups`. Not a PVE HA resource. Backup and protection are open #625 decisions. |
| NetBird | 0.79.0 from the `pkgs.netbird.io` `stable` repository; kernel WireGuard on `wt0`, UDP 51820; client routes and DNS disabled, all other client settings default |
| Tags | `netbird;pilot;remote-access` |

Main cannot reach the VM's SSH. Manage it with `qm guest exec 103` from `pve-sbx-1`, as the runbook describes.

## Vendor data

[`vendor-data.yaml`](vendor-data.yaml) is byte-identical to the live snippet `library-pve:snippets/netbird-pilot-router.yaml`. Both had SHA-256 `1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a` on 2026-10-27. It does the following:

- installs `ca-certificates`, `curl`, `qemu-guest-agent` and `tcpdump`;
- enables IPv4 forwarding;
- installs NetBird from its Debian repository after checking the repository key against a pinned SHA-256.

It contains no credentials or enrollment material. If NetBird rotates its repository key, the check fails and the bootstrap stops before installing anything. Update the pin only after confirming the new key from NetBird's documentation.

Edit this file and the live snippet together, and compare their hashes afterwards:

```sh
shasum -a 256 proxmox/guests/netbird-router/vendor-data.yaml
ssh kelchm@10.32.20.21 sudo sha256sum /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml
```

## Rebuild

The original creation commands were not recorded. The steps below reproduce the configuration that was read back. While VM 103 is down, NetBird has no routed remote path, so rebuild it from Main with someone at home.

1. Confirm that the live snippet matches this directory's copy. If it is missing, copy `vendor-data.yaml` to `/mnt/pve/library-pve/snippets/netbird-pilot-router.yaml`; the share is mounted on every node.
2. If a broken VM 103 still exists, confirm its name and node before removing it: `sudo qm destroy 103 --purge 1 --destroy-unreferenced-disks 1`.
3. On `pve-sbx-1`, write the operator key from `sudo qm config 200` (its `sshkeys` value, URL-decoded) to a file, then run:

   ```sh
   sudo qm clone 9000 103 --name netbird-pilot-router --full 1 --storage local-lvm
   sudo qm set 103 \
     --cores 1 --memory 1024 \
     --net0 virtio,bridge=vmbr0,tag=19 \
     --ipconfig0 ip=10.32.19.103/24,gw=10.32.19.1 \
     --nameserver 10.32.19.1 --searchdomain home.kelch.io \
     --ciuser kelchm --sshkeys <key-file> \
     --cicustom vendor=library-pve:snippets/netbird-pilot-router.yaml \
     --onboot 1 --startup order=10,up=30 \
     --tags 'netbird;pilot;remote-access'
   sudo qm disk resize 103 scsi0 8G
   sudo qm start 103
   ```

4. Once the guest agent answers, run these through `sudo qm guest exec 103 --` and check each `exitcode` and `out-data`: `cloud-init status --wait`, `systemctl --failed`, `systemctl is-active qemu-guest-agent netbird`, `sysctl net.ipv4.ip_forward` and `netbird version`. A rebuild installs NetBird's current stable release, not 0.79.0.

## Enrollment

Enrollment needs an administrator in the NetBird dashboard. Git and cloud-init never hold a setup key.

1. In the dashboard, delete the previous instance's peer from **Peers**, if it is still listed.
2. Create a setup key with a usage limit of 1, a short expiry and auto-group `pilot-routers`.
3. Enroll the VM:

   ```sh
   sudo qm guest exec 103 --timeout 120 -- netbird up --setup-key <key> --disable-client-routes --disable-dns
   ```

   The key is visible in the PVE host's process list while the command runs, which the single use and short expiry contain.
4. Revoke the key if enrollment failed.

The `pilot-home` network routes through the `pilot-routers` group, not a specific peer, so the new peer takes over the routes once it is in that group. The UniFi rules match source `10.32.19.103`, so a rebuild at the same address needs no firewall change.

Verify with `netbird status`. It must show Management connected and `Networks: 10.32.130.1/32, 10.32.140.1/32, 10.32.30.1/32`. Then run the router probes in the runbook's verification section, and read NetBird back with [`scripts/netbird-export.sh`](../../../scripts/netbird-export.sh).

## Upgrades

The vendor data sets `package_upgrade: false`, and Debian's unattended-upgrades ignore the NetBird repository. NetBird therefore stays at its installed version until someone upgrades it:

```sh
sudo qm guest exec 103 --timeout 300 -- sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --yes netbird'
```

Treat an upgrade as a router restart: away clients lose the home resources until the daemon reconnects. Run it as its own change with someone at home, then repeat the enrollment checks.
