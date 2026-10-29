# NetBird routing peer

VM 103 is NetBird's only routing peer. It carries traffic from away clients to the household HTTPS, admin HTTPS and DNS resources. NetBird Cloud objects, client ownership, access verification and recovery limits are in the [NetBird runbook](../../../docs/runbooks/netbird-remote-access.md); setup and qualification are tracked in [#625](https://github.com/kelchm/home-lab/issues/625).

Read back on 2026-10-27, with backup/protection changes verified on 2026-10-28 and node-local snippet installation and the first scheduled post-enrollment backup verified on 2026-10-29:

| Setting | Value |
|---|---|
| VMID and name | `103`, `netbird-pilot-router`. Dropping `pilot` is approved in stages: friendly labels first; the VM name and snippet only with the router upgrade after the R1 baseline (#625). |
| Node | `pve-sbx-1`. Template 9000 is on that node's `local-lvm`, so a rebuild clones there. |
| Network | `10.32.19.103/24` on VLAN 19 (`vmbr0` tag 19); gateway and resolver `10.32.19.1`; search domain `home.kelch.io` |
| Guest | Full clone of template 9000 (Debian 13 genericcloud): 1 vCPU, 1 GiB RAM, 8 GiB `local-lvm`, OVMF, QEMU guest agent |
| Boot | `onboot: 1`, `startup: order=10,up=30`; unattended host-boot acceptance is still pending (#1096). |
| Protection, backup, HA | `protection: 1` and covered by `daily-backups`, both since 2026-10-28. Not a PVE HA resource. |
| NetBird | 0.79.0 from the `pkgs.netbird.io` `stable` repository; kernel WireGuard on `wt0`, UDP 51820; client routes and DNS disabled, all other client settings default |
| Tags | `netbird;pilot;remote-access` |

Main cannot reach the VM's SSH. Manage it with `qm guest exec 103` from `pve-sbx-1`, as the runbook describes.

## Vendor data

[`vendor-data.yaml`](vendor-data.yaml) is byte-identical to the installed snippet referenced by VM 103's saved `local:snippets/netbird-pilot-router.yaml` and the retained NAS source `library-pve:snippets/netbird-pilot-router.yaml`. All have SHA-256 `1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a`. The [node-local replacement](local-startup.md#accepted-installation-2026-10-29) was installed and verified on 2026-10-29 without a restart or running-medium regeneration, preserving hostname, instance ID, enrolled peer identity and NetBird 0.79.0. The cloud-init image still shows the expected old NAS value with the local value pending until a normal start; autostart acceptance remains untested in #1096. The vendor data does the following:

- installs `ca-certificates`, `curl`, `qemu-guest-agent` and `tcpdump`;
- enables IPv4 forwarding;
- installs NetBird from its Debian repository after checking the repository key against a pinned SHA-256.

It contains no credentials or enrollment material. If NetBird rotates its repository key, the check fails and the bootstrap stops before installing anything. Update the pin only after confirming the new key from NetBird's documentation.

Treat snippet edits as a separate reviewed change. The installed copy and retained NAS source were verified byte-identical at installation; the commands below compare the repository file with that retained NAS source:

```sh
shasum -a 256 proxmox/guests/netbird-router/vendor-data.yaml
ssh kelchm@10.32.20.21 sudo sha256sum /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml
```

## Restore

Prefer a successful post-enrollment `daily-backups` archive to rebuilding. The restore procedure is intended to recover the archived NetBird peer identity and installed version; if that identity remains enrolled, no setup key is needed. The first scheduled post-enrollment backup was verified on 2026-10-29: `backups-pve-sbx:backup/vzdump-qemu-103-2026_10_29-05_00_02.vma.zst`, 623,690,275 bytes, VM 103 backup 09:00:02–09:00:17 UTC, enclosing task `OK` at 09:01:44 UTC. It follows enrollment on September 27 at about 15:28; the older September 27 05:00 archive predates enrollment. Today's archive predates the 11:56 UTC local-snippet installation. It was not unpacked, restored or booted, so neither its saved vendor reference nor identity recovery was verified by this check. Restore remains undrilled. While VM 103 is down, NetBird has no routed remote path. Run the following on `pve-sbx-1` through a reachable admin session on Main, with someone at home.

```sh
sudo pvesm list backups-pve-sbx --vmid 103
sudo qm stop 103
sudo qm set 103 --protection 0
sudo qmrestore /mnt/pve/backups-pve-sbx/dump/<post-enrollment-archive> 103 --force 1 --storage local-lvm
sudo qm set 103 --protection 1
```

Before `qm start`, read the restored `cicustom` and resolve every referenced snippet with `sudo pvesm path <volume-id>`. Require the resolved files to exist; compare the vendor file’s SHA-256 with the pinned repository copy, and validate any additional custom files against their reviewed archive inputs. Snippets are external files, not restored by the VM disk archive. Older archives still name `library-pve:snippets/netbird-pilot-router.yaml`: keep that NAS file available while those archives remain. For an archive naming `local:snippets/netbird-pilot-router.yaml`, enable `snippets` on `local` and place the byte-identical root-owned file on the chosen restore node before starting, using the [destination provisioning checks](local-startup.md#provision-on-another-node), without running the existing-VM reference-change commands. Preserve the existing cluster-wide content flags rather than assuming they still match the pre-installation baseline. Cluster-wide storage configuration does not copy the node-local file. Keep all other restored settings intact; do not combine identity recovery with an unreviewed snippet-path change. Never start a restored copy beside the original enrolled peer.

After the snippet-reference checks pass, start it with `sudo qm start 103`. PVE refuses to restore over a protected or running VM. Don't pass `--unique`: the new MAC address it assigns is part of the cloud-init network data, so the next start would become a new cloud-init instance and upgrade NetBird (see [below](#cloud-init-settings-upgrade-netbird)). Then verify `netbird status`, the existing peer enrollment and the router probes described below. Re-enroll only if the restored identity is no longer enrolled.

## Rebuild

The original creation commands were not recorded. The steps below reproduce the configuration that was read back. While VM 103 is down, NetBird has no routed remote path, so rebuild it from Main with someone at home.

1. Choose the snippet reference explicitly. The commands below retain the original NAS-backed reference. To reproduce the installed node-local configuration, enable `snippets` on `local`, provision the byte-identical root-owned file on the chosen node using the [destination provisioning checks](local-startup.md#provision-on-another-node), and substitute `vendor=local:snippets/netbird-pilot-router.yaml` below. Do not run the existing-VM installation commands against a newly rebuilt VM. If deliberately using the NAS-backed reference, verify the NAS copy against `vendor-data.yaml`; if missing, restore that exact file to `/mnt/pve/library-pve/snippets/netbird-pilot-router.yaml`. Retain it for older archives in either case.
2. If a broken VM 103 still exists, confirm its name and node, turn off protection with `sudo qm set 103 --protection 0`, then remove it: `sudo qm destroy 103 --purge 1 --destroy-unreferenced-disks 1`.
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
     --onboot 1 --startup order=10,up=30 --protection 1 \
     --tags 'netbird;pilot;remote-access'
   sudo qm disk resize 103 scsi0 8G
   sudo qm start 103
   ```

4. Once the guest agent answers, run these through `sudo qm guest exec 103 --` and check each `exitcode` and `out-data`: `cloud-init status --wait`, `systemctl --failed`, `systemctl is-active qemu-guest-agent netbird`, `sysctl net.ipv4.ip_forward` and `netbird version`. A rebuild installs NetBird's current stable release, not 0.79.0.

## Enrollment

Follow the [stdin-only enrollment procedure](enrollment.md), including separately staging its credential-free helper for a rebuild, fresh logging checks, bounded guest-exec completion and end-of-window key revocation on every outcome. Git and cloud-init never hold a setup key. The frozen vendor snippet is unchanged. The former argv-based example could persist in guest journal/sudo logs and process lists; actual historical log contents are unknown, and the recorded old one-off key is expired.

The `pilot-home` network routes through the `pilot-routers` group, not a specific peer, so the new peer takes over the routes once it is in that group. The UniFi rules match source `10.32.19.103`, so a rebuild at the same address needs no firewall change.

Verify with `netbird status`. It must show Management connected and `Networks: 10.32.130.1/32, 10.32.140.1/32, 10.32.30.1/32`. Then run the router probes in the runbook's verification section, and read NetBird back with [`scripts/netbird-export.sh`](../../../scripts/netbird-export.sh).

## Upgrades

Debian's unattended-upgrades ignore the NetBird repository, so NetBird stays at its installed version until someone upgrades it:

```sh
sudo qm guest exec 103 --timeout 300 -- sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --yes netbird'
```

Treat an upgrade as a router restart: away clients lose the home resources until the daemon reconnects. Run it as its own change with someone at home, then repeat the enrollment checks.

### Cloud-init settings upgrade NetBird

The vendor data's `package_upgrade: false` has no effect. PVE's generated user data sets `package_upgrade: true`, its `ciupgrade` default, and user data takes precedence. The first boot of each cloud-init instance therefore runs `apt-get dist-upgrade`, then the vendor bootstrap's `apt-get install netbird`; VM 103's first boot on 2026-09-27 did both.

A byte-identical vendor-only path change is different: vendor content and its storage reference are excluded from the generated instance ID. The [node-local startup procedure](local-startup.md) verifies user/network/metadata hashes without a restart and leaves acceptance for the next planned authorized host reboot.

PVE derives the instance ID from the user and network data, and regenerates them each time it starts the VM. They hold the VM name as the hostname, the cloud-init user, its SSH keys and the network settings. Changing any of these makes the next start, including one after a host reboot, a new instance: it upgrades every package including NetBird, and applies any new hostname. Make such a change only as part of a planned upgrade, and restart VM 103 immediately afterwards rather than leaving it for the next host reboot. This was read from PVE 9.2.21 and the guest's cloud-init and apt logs on 2026-10-28.
