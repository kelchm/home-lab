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

Direct root SSH remains enabled for break-glass recovery and the encrypted configuration-capture workflow. Do not remove it until those paths have been migrated and tested through a narrower recovery identity.

## Shared storage

| PVE storage ID | Current NFS export | Content |
|---|---|---|
| `library-pve` | `10.32.25.5:/volume1/library-pve` | ISO images, CT templates, snippets, and imports |
| `backups-pve-sbx` | `10.32.25.5:/volume1/backups-pve-sbx` | VZDump backups |

Both stores use hard NFSv4.1 mounts and are allowed only from the three PVE storage addresses. `library-pve` is platform-specific but reusable across PVE clusters; `backups-pve-sbx` is intentionally cluster-specific. Shared external storage uses the DSM shared-folder leaf as its PVE storage ID so the same resource has one canonical name across both systems.

The cluster job `daily-backups` backs up all guests to `backups-pve-sbx` at 05:00 in snapshot mode with Zstandard compression. Its retention policy is last 3, daily 7, weekly 4, and monthly 6. Disposable guests must be excluded deliberately rather than relying on tags. The built-in matcher currently targets `mail-to-root`, but direct delivery to Gmail failed with `550 5.7.1`; do not depend on email alerts until an authenticated SMTP relay is configured and tested.

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
journalctl -u corosync --since today --no-pager
```

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

As of 2026-08-27 14:48 EDT, commissioning had observed three correctable AER events on node 1, two on node 2, and two on node 3 at the SN770 endpoint. The first two node-1 events and both node-2 events paired a root-port Data Link Layer timeout with an endpoint Physical Layer `RxErr`; the later node-1 event and both node-3 events contained the endpoint `RxErr` without a root-port timeout. Node 3's two events appeared after it had completed a bounded 100-cycle idle-to-read screen without reproducing one, so that screen is not sufficient evidence of stability. All three drives still reported zero SMART critical warnings, media errors, and NVMe error-log entries, with no reset, controller loss, namespace loss, or guest I/O error. APST and PCIe L1 remain at their defaults; no unsupported power-management workaround has been applied.

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
test -z "$(qm config RESTORE_VMID | sed -n 's/^\(net[0-9]\+\):.*/\1/p')"
qm config RESTORE_VMID
qm start RESTORE_VMID
qm guest cmd RESTORE_VMID ping
```

After validating the recovered filesystem and application state, shut down the isolated copy. Destroy it only when the VMID, node, and disposable purpose have been rechecked.

The initial acceptance drill used disposable VM 300. A 60-second 70/30 random read/write `fio` run on node 1 completed without error, an online local-disk migration to node 2 used VLAN 25 with 252 ms guest downtime, and a guest-agent-consistent 334 MB VZDump archive completed in 14 seconds. The archive restored on node 3 as VM 301, booted with its NIC removed, and passed filesystem and QEMU guest-agent checks. Both disposable VMs were then destroyed; `backups-pve-sbx:backup/vzdump-qemu-300-2026_08_27-11_35_57.vma.zst` remains as the recovery artifact. Restore elapsed time was not captured, so the RTO/restore-throughput acceptance measurement remains open.

## Host baseline artifacts

[`host/pve-no-subscription-popup`](host/pve-no-subscription-popup) removes only the subscription modal from the PVE web client. It does not falsify subscription status or enable enterprise repositories. The script hashes and preserves each upstream file before making an exact, fail-closed replacement; [`host/99-pve-no-subscription-popup`](host/99-pve-no-subscription-popup) reapplies it after package transactions.

Apply the reviewed artifacts to one node, verify the marker, and then repeat one node at a time:

```sh
scp proxmox/host/pve-no-subscription-popup root@NODE:/tmp/pve-no-subscription-popup
scp proxmox/host/99-pve-no-subscription-popup root@NODE:/tmp/99-pve-no-subscription-popup
ssh root@NODE 'install -o root -g root -m 0755 /tmp/pve-no-subscription-popup /usr/local/sbin/pve-no-subscription-popup && install -o root -g root -m 0644 /tmp/99-pve-no-subscription-popup /etc/apt/apt.conf.d/99-pve-no-subscription-popup && /usr/local/sbin/pve-no-subscription-popup'
ssh root@NODE "grep -F 'Local no-subscription policy' /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js"
```

Package changes abort the hook if the expected upstream JavaScript structure changes. If that blocks package recovery, move `/etc/apt/apt.conf.d/99-pve-no-subscription-popup` to `/root/99-pve-no-subscription-popup.disabled`, finish repairing the package state, review and update the exact replacement, rerun the script, and reinstall the hook. Do not weaken the guard or leave the hook disabled silently.

## Configuration recovery archive

[`capture-host-config.sh`](capture-host-config.sh) streams cluster and node configuration directly over SSH into age-encrypted archives under the ignored `.private/pve-sbx/recovery/` directory. The plaintext tar stream is never written to the workstation. The capture includes `/etc/pve`, a SQLite-consistent snapshot of `/var/lib/pve-cluster/config.db`, Corosync authentication, system account and sudo state, root and operator SSH authorization, network and repository state, SSH host identity, LVM metadata, installed package versions, the kernel boot log, and NVMe identity and health data, so treat the resulting archive as a secret even though it is encrypted.

```sh
PVE_AGE_IDENTITY=age.key ./proxmox/capture-host-config.sh
```

The script decrypts and validates required members in each archive before accepting it, then prints its SHA-256 digest. A verified three-node capture, including SQLite-consistent pmxcfs database snapshots and the host operator identity, was completed on 2026-08-27 at `20260827T185913Z`. This is off-node recovery state on the admin workstation, not an off-site backup; copy the encrypted artifacts to a second protected location if workstation loss is in scope.

## Outstanding commissioning gates

- Investigate and resolve the correctable SN770 PCIe/AER events observed on all three nodes under default APST and PCIe L1 power management, then repeat the idle and I/O acceptance window on all three hosts. Node 3's clean 100-cycle screen preceded two later errors and is not sufficient evidence for the gate.
- Repeat a timed isolated restore after the storage investigation and record restore RTO and throughput; the initial restore proved functionality but did not capture elapsed time.
- Complete physical cold-power removal and recovery on all three nodes; GLKVM cannot assert ATX power.
- Remove the obsolete SanDisk qualification USB from node 1 after its NVMe cold-power boot is proven.
- Enroll TOTP for `kelchm@pve`.
- Create a dedicated Cloudflare DNS token, configure ACME DNS-01 certificates for each node FQDN, and avoid reusing the Kubernetes cert-manager token.
- Configure and test an authenticated SMTP notification target; direct-to-MX Postfix delivery from the home public IP is rejected by Gmail.

Installation, BIOS, KVM, PXE, and recovery-console procedures live in [the PVE node bootstrap runbook](../docs/runbooks/pve-node-bootstrap.md). The architecture and acceptance rationale remain in [the cluster plan](../docs/plans/20260814-pve-cluster.md).
