# Proxmox VE

This directory holds the Git-managed baseline and operator documentation for the `pve-sbx` cluster. PVE is intentionally independent of Flux and Kubernetes: changes are reviewed here, then applied deliberately from an admin workstation or directly on a node.

## Live cluster

| Node | Management | Storage and migration |
|---|---|---|
| `pve-sbx-1` | `10.32.20.21` | `10.32.25.21` |
| `pve-sbx-2` | `10.32.20.22` | `10.32.25.22` |
| `pve-sbx-3` | `10.32.20.23` | `10.32.25.23` |

The cluster name is `pve-sbx`. Corosync link 0 uses the management addresses with priority 20; link 1 uses the physically separate 2.5 GbE storage addresses with priority 10. Secure migration is pinned to `10.32.25.0/24`.

Use an individual node URL such as `https://pve-sbx-1.home.kelch.io:8006/`; there is no cluster VIP. The local `kelchm@pve` administrator exists, but TOTP enrollment is still pending. Root and bootstrap credentials stay outside Git.

## Operator access

The PVE realm account `kelchm@pve` controls UI and API authorization; it is separate from the Linux account on each host. All three nodes also have a consistent `kelchm` Unix account at UID/GID 1000, with a locked password, the `personal:home-lab` ED25519 public key, membership in `sudo`, and an exact `/etc/sudoers.d/90-kelchm` rule granting noninteractive administrative access. Use it for normal host sessions:

```sh
ssh kelchm@NODE
sudo -i
```

PVE retains its cluster-managed `root@pve-sbx-*` SSH keys for node-to-node operations. The human `personal:home-lab` key is not authorized directly for root; ordinary administration and the encrypted configuration-capture workflow connect as `kelchm` and elevate with sudo.

## Certificates

Each node serves an independently issued Let's Encrypt certificate for its exact `pve-sbx-N.home.kelch.io` FQDN. The cluster ACME account is `letsencrypt-production`, and the DNS-01 plugin is `pve-sbx-acme-dns01`, limited to the three PVE nodes and backed by a dedicated Cloudflare token with DNS Edit and Zone Read only for `kelch.io`. The authoritative token remains in 1Password; PVE's encrypted recovery capture contains the cluster runtime copy. Do not reuse the Kubernetes cert-manager credential.

PVE's active `pve-daily-update.timer` handles renewal. Check node configuration, timer state, and the certificate actually served on port 8006:

```sh
ssh kelchm@NODE "sudo pvenode config get | grep -E '^acme|^acmedomain'; systemctl is-active pve-daily-update.timer"
openssl s_client -connect NODE_IP:8006 -servername NODE_FQDN -verify_hostname NODE_FQDN </dev/null
```

The initial certificates were issued and hostname-validated on 2026-08-27. A manual `pvenode acme cert renew` correctly refuses while a certificate is outside PVE's 30-day renewal window; do not use `--force` merely to test routine renewal.

## Shared storage

| PVE storage ID | Current NFS export | Content |
|---|---|---|
| `library-pve` | `10.32.25.5:/volume1/library-pve` | ISO images, CT templates, snippets, and imports |
| `backups-pve-sbx` | `10.32.25.5:/volume1/backups-pve-sbx` | VZDump backups |

Both stores use hard NFSv4.1 mounts and are allowed only from the three PVE storage addresses. `library-pve` is platform-specific but reusable across PVE clusters; `backups-pve-sbx` is intentionally cluster-specific. Shared external storage uses the DSM shared-folder leaf as its PVE storage ID so the same resource has one canonical name across both systems.

The cluster job `daily-backups` backs up all non-disposable guests to `backups-pve-sbx` at 05:00 America/New_York in snapshot mode with Zstandard compression. The live job uses `all=1`; add every disposable VMID to its explicit `exclude` field. The field is currently unset because no disposable guest remains. Its retention policy is last 3, daily 7, weekly 4, and monthly 6. The built-in matcher currently targets `mail-to-root`, but direct delivery to Gmail failed with `550 5.7.1`; do not depend on email alerts until an authenticated SMTP relay is configured and tested.

## Guest trunk

Every node's RTL8125-backed `vmbr0` is VLAN-aware with an explicit `bridge-vids 10 19 21 25 90` allowlist. The corresponding UniFi `pve-guest-trunk` profile has no native network and carries only those five tagged VLANs on Lab Switch ports 13–15. An untagged guest therefore fails closed, and adding a new guest VLAN requires an intentional change on both PVE and UniFi.

## Persistent guests

| VMID | Guest | Address | Purpose | Source |
|---|---|---|---|---|
| `101` | `tailscale-router-1` | `10.32.19.101` | Tailscale subnet-router candidate 1 | [`guests/tailscale-router`](guests/tailscale-router/) |
| `102` | `tailscale-router-2` | `10.32.19.102` | Tailscale subnet-router candidate 2 | [`guests/tailscale-router`](guests/tailscale-router/) |
| `200` | `hermes-1` | `10.32.21.100` | Hermes Agent, dashboard, and client API | [`guests/hermes-1`](guests/hermes-1/) |

All three guests are enabled at host boot, covered by `daily-backups`, and intentionally not PVE HA resources. The Tailscale routers live on different PVE nodes and provide application-level failover by advertising the same route set; PVE must not restart both onto one surviving host. `hermes-1` keeps its application state on its local VM disk under `/srv/hermes`.

## Applied guest network policy

Three UniFi policies contain the general-purpose Workloads VLAN without changing Main administration, `services-prod`, or Internet access:

| Policy | Source | Destination | Action |
|---|---|---|---|
| `Block Workloads to Protected Networks` | `Workloads` | `Infra Mgmt`, `Storage`, `K8s Prod` | Block all |
| `Block Workloads to Admin Prod Routed` | `Workloads` | `10.32.130.0/24` in UniFi's `External` destination zone | Block all |
| `Block K8s Prod to Workloads` | `K8s Prod` | `Workloads` | Block all |

The `admin-prod` pool is a BGP-routed prefix rather than a UniFi network. Network 10.5.67 classified that path in the `External` destination zone during live testing; an otherwise identical `Internal`-zone rule did not match `10.32.130.1`. Keep the explicit routed rule above the default allow rules and retest after UniFi upgrades.

Validated from a disposable VLAN 21 guest on 2026-08-27: PVE management and storage addresses, NAS NFS, the Talos API, and `admin-prod` were blocked; `services-prod`, local DNS, and Internet HTTP remained reachable. A pod on `k8s-prod` could not initiate TCP to the guest, while the Main admin workstation retained SSH access. The complete applied record lives in [`network/unifi/README.md`](../network/unifi/README.md).

## Routine checks

Run cluster-wide checks from any node:

```sh
pvecm status
pvecm nodes
pvesm status
pvesh get /cluster/backup
pvesh get /cluster/resources --type vm
```

Confirm both Corosync links from every node before maintenance:

```sh
pvecm status
corosync-cfgtool -s
corosync-cfgtool -n
journalctl -u corosync --since today --no-pager
```

Inspect the output on each node and confirm that Link 0 and Link 1 report every peer as connected. `pvecm status` proves membership and quorum, and the journal provides history, but neither substitutes for the live per-link output. Do not use the `corosync-cfgtool` exit status alone as proof of link health.

Check the local host before and after storage-heavy work:

```sh
systemctl --failed
journalctl -k -b --no-pager | grep -Ei 'nvme|PCIe|AER|I/O error|reset|timeout'
nvme smart-log /dev/nvme0
nvme error-log /dev/nvme0
pvesm status
lvs -a -o lv_name,lv_size,data_percent,metadata_percent
```

Any NVMe reset, timeout, namespace loss, media error, or PCIe/AER event fails the storage acceptance gate and must be investigated rather than cleared from the log.

As of 2026-08-27 14:48 EDT, commissioning had observed three correctable AER events on node 1, two on node 2, and two on node 3 at the SN770 endpoint. The first two node-1 events and both node-2 events paired a root-port Data Link Layer timeout with an endpoint Physical Layer `RxErr`; the later node-1 event and both node-3 events contained the endpoint `RxErr` without a root-port timeout. Node 3's two events appeared after it had completed a bounded 100-cycle idle-to-read screen without reproducing one, so that screen is not sufficient evidence of stability. All three drives still reported zero SMART critical warnings, media errors, and NVMe error-log entries, with no reset, controller loss, namespace loss, or guest I/O error.

At 17:26 EDT, firmware-level **PCI Express Power Management** was disabled on all three nodes, one node and reboot at a time. Post-boot `lspci` showed the `00:1d.0` root port as `ASPM not supported` and `ASPM Disabled`, and the `02:00.0` SN770 endpoint as `ASPM Disabled`; Linux NVMe APST remained at its default `100000` µs latency threshold. Every node rejoined both cluster quorum and active NFS storage with zero failed units, zero SMART critical warnings, zero media or NVMe error-log entries, and no PCIe, NVMe, reset, timeout, or I/O error in the new boot. This is the applied mitigation baseline, not clearance of the zero-AER gate; the three-node idle and I/O acceptance window must still be repeated.

## Backup and isolated restore drill

Create an on-demand snapshot backup:

```sh
vzdump VMID --storage backups-pve-sbx --mode snapshot --compress zstd
pvesm list backups-pve-sbx --vmid VMID
```

Restore to an unused VMID on the intended node, then remove or disconnect every NIC before first boot. Never boot a restored copy beside its source with the same network identity.

```sh
qmrestore /mnt/pve/backups-pve-sbx/dump/ARCHIVE.vma.zst RESTORE_VMID --storage local-lvm --unique 1
for nic in $(qm config RESTORE_VMID | sed -n 's/^\(net[0-9]\+\):.*/\1/p'); do
    qm set RESTORE_VMID --delete "$nic"
done
restored_config=$(qm config RESTORE_VMID) || { echo "Unable to read restored VM configuration" >&2; exit 1; }
remaining_nics=$(printf '%s\n' "$restored_config" | sed -n 's/^\(net[0-9]\+\):.*/\1/p')
if [ -n "$remaining_nics" ]; then
    echo "Refusing to start RESTORE_VMID: network interfaces remain: $remaining_nics" >&2
    exit 1
fi
printf '%s\n' "$restored_config"
qm start RESTORE_VMID
qm guest cmd RESTORE_VMID ping
```

After validating the recovered filesystem and application state, shut down the isolated copy. Destroy it only when the VMID, node, and disposable purpose have been rechecked.

The initial acceptance drill used disposable VM 300. A 60-second 70/30 random read/write `fio` run on node 1 completed without error, an online local-disk migration to node 2 used VLAN 25 with 252 ms guest downtime, and a guest-agent-consistent 334 MB VZDump archive completed in 14 seconds. The archive restored on node 3 as VM 301, booted with its NIC removed, and passed filesystem and QEMU guest-agent checks. Both disposable VMs were then destroyed; `backups-pve-sbx:backup/vzdump-qemu-300-2026_08_27-11_35_57.vma.zst` remains as the recovery artifact. Restore elapsed time was not captured, so the RTO/restore-throughput acceptance measurement remains open.

## Host baseline artifacts

[`host/pve-remove-nag.sh`](host/pve-remove-nag.sh) and [`host/no-nag-script`](host/no-nag-script) are copied verbatim from the Proxmox VE Helper-Scripts post-install implementation at commit [`519f5630cecdcf030a22934cba815c6c1dde2b6d`](https://github.com/community-scripts/ProxmoxVE/blob/519f5630cecdcf030a22934cba815c6c1dde2b6d/tools/pve/post-pve-install.sh#L559-L610). They suppress the desktop and mobile subscription reminders and reapply the maintained patch after package transactions. They do not enable enterprise repositories. The upstream MIT license is retained in [`host/LICENSE.community-scripts`](host/LICENSE.community-scripts).

Restore the packaged widget toolkit before applying the artifacts so a previous or changed patch is not layered underneath. Apply and verify one node at a time:

```sh
scp proxmox/host/pve-remove-nag.sh proxmox/host/no-nag-script kelchm@NODE:/tmp/
ssh kelchm@NODE 'sudo rm -f /etc/apt/apt.conf.d/99-pve-no-subscription-popup /usr/local/sbin/pve-no-subscription-popup && sudo apt-get install --reinstall -y proxmox-widget-toolkit && sudo install -o root -g root -m 0755 /tmp/pve-remove-nag.sh /usr/local/bin/pve-remove-nag.sh && sudo install -o root -g root -m 0644 /tmp/no-nag-script /etc/apt/apt.conf.d/no-nag-script && sudo /usr/local/bin/pve-remove-nag.sh'
ssh kelchm@NODE "sudo grep -F NoMoreNagging /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js && sudo grep -F 'MANAGED BLOCK FOR MOBILE NAG' /usr/share/pve-yew-mobile-gui/index.html.tpl"
```

After installation, clear the browser cache or perform an empty-cache hard reload before testing a new login. If a future PVE update defeats the suppression, check the maintained upstream implementation and review its current diff before refreshing these pinned artifacts.

## Configuration recovery archive

[`capture-host-config.sh`](capture-host-config.sh) streams cluster and node configuration directly over SSH into age-encrypted archives under the ignored `.private/pve-sbx/recovery/` directory. The plaintext tar stream is never written to the workstation. The capture includes `/etc/pve`, a SQLite-consistent snapshot of `/var/lib/pve-cluster/config.db`, the ACME account, DNS plugin credential and proxy certificate keys, Corosync authentication, system account and sudo state, root and operator SSH authorization, network and repository state, SSH host identity, LVM metadata, installed package versions, the kernel boot log, and NVMe identity and health data, so treat the resulting archive as a secret even though it is encrypted.

```sh
PVE_AGE_IDENTITY=age.key ./proxmox/capture-host-config.sh
```

The script connects as `kelchm` by default and requires the verified noninteractive sudo policy. Set `PVE_SSH_USER` only for an explicitly tested recovery identity; direct human root SSH is not the normal path.

The script decrypts and validates required members in each archive before accepting it, then prints its SHA-256 digest. A verified three-node capture, including SQLite-consistent pmxcfs database snapshots, the host operator identity, the installed subscription-nag helper and package hook, and the ACME account, plugin, certificate and key material, was completed on 2026-08-27 at `20260827T200605Z`. This is off-node recovery state on the admin workstation, not an off-site backup; copy the encrypted artifacts to a second protected location if workstation loss is in scope.

## Outstanding commissioning gates

- Validate the firmware-level PCIe ASPM disable applied to all three nodes by repeating the idle and I/O acceptance window on every host while leaving NVMe APST unchanged. Node 3's earlier clean 100-cycle screen preceded two later errors and is not sufficient evidence for the gate.
- Repeat a timed isolated restore after the storage investigation and record restore RTO and throughput; the initial restore proved functionality but did not capture elapsed time.
- Complete physical cold-power removal and recovery on all three nodes; GLKVM cannot assert ATX power.
- Remove the obsolete SanDisk qualification USB from node 1 after its NVMe cold-power boot is proven.
- Enroll TOTP for `kelchm@pve`.
- Configure and test an authenticated SMTP notification target; direct-to-MX Postfix delivery from the home public IP is rejected by Gmail.

Installation, BIOS, KVM, PXE, and recovery-console procedures live in [the PVE node bootstrap runbook](../docs/runbooks/pve-node-bootstrap.md). The architecture and acceptance rationale remain in [the cluster plan](../docs/plans/20260814-pve-cluster.md).
