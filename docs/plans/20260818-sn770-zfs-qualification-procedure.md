# SN770 ZFS qualification procedure

**Status:** Implemented — 2026-08-22; executed against one specimen. Results: [qualification record](../sn770-zfs-qualification.md).

## 1. Safety and test topology

Use only the uncommissioned PVE machines. Do not run this on the live Talos nodes.

Preferred setup:

- Install temporary PVE on the already-authorized separate SanDisk USB thumb drive on `pve-lab-1`.
- Leave the internal SN770 entirely disposable and available as a raw ZFS test device.
- Keep logs on the USB boot disk where practical and stream kernel logs to another machine.
- Have local console access and the ability to remove power.
- Do not form the PVE cluster yet.

A separate boot disk is important: if the NVMe controller disappears, the host and logs remain accessible.

Do not:

- Put valuable data on the SN770.
- Test through a USB NVMe enclosure.
- Change sector size except for the explicitly isolated 4 KiB reproduction arm in section 4A.
- Disable APST/ASPM or force PCIe speed during the primary experiment.
- Use the unofficial Linux firmware procedure from the gist.
- Upgrade all three drives before collecting 731100WD baselines.

## 2. Assign the drives

After physical inventory:

| Host | Role |
|---|---|
| `pve-lab-1` | Primary same-drive firmware A/B test |
| `pve-lab-2` | Hold on 731100WD as a control while node 1 runs new firmware |
| `pve-lab-3` | Independent final qualification |

Stop and redesign the experiment if the machines do not contain the expected 1 TB SN770s or do not begin on comparable firmware.

## 3. Capture the untouched baseline

Boot the intended current PVE kernel from the separate USB boot disk. Before writing to the NVMe, collect:

```bash
mkdir -p /root/sn770-results/baseline

uname -a
pveversion -v
zfs version
cat /proc/cmdline

nvme list
nvme id-ctrl /dev/nvme0
nvme id-ns -H /dev/nvme0n1
nvme smart-log /dev/nvme0
nvme error-log /dev/nvme0 -e 256
smartctl -x /dev/nvme0

lsblk -o NAME,MODEL,SERIAL,FWWN,SIZE,PHY-SEC,LOG-SEC
lspci -nnvv
journalctl -k -b

cat /sys/module/nvme/parameters/max_host_mem_size_mb
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
cat /sys/module/nvme_core/parameters/io_timeout
cat /sys/block/nvme0n1/queue/scheduler
```

Extract and record these controller fields explicitly:

```bash
nvme id-ctrl /dev/nvme0 |
  grep -E '^(mn|sn|fr|hmpre|hmmin|hmminds|hmmaxd)'
```

Verify IOMMU operation:

```bash
journalctl -k -b | grep -Ei 'DMAR|IOMMU'
find /sys/kernel/iommu_groups -type l | head
```

Identify the NVMe PCI address:

```bash
basename "$(readlink -f /sys/class/nvme/nvme0/device)"
```

Expected old-firmware values are approximately:

```text
HMPRE   51200 = 200 MiB
HMMIN     823 = 3.21484375 MiB
HMMINDS     0
HMMAXD      8
```

Do not assume 731150WD reports the same values; comparison is one of the test outputs.

### Mandatory destructive-target gate

Before any destructive command, manually verify:

```text
Host serial:       8CG7466C9K
NVMe model:        WD_BLACK SN770 1TB
NVMe serial:       23030W800174
```

Resolve the exact persistent by-id path and verify it resolves to that model and
serial. Never substitute an unverified `/dev/sdX` or `/dev/nvme0n1` path. The
USB thumb drive is the OS disk; the SN770 is not an installer target.

## 4. Establish remote observation

On another machine, preserve the kernel stream outside the test host:

```bash
mkdir -p sn770-results/pve-lab-1/731100WD/hmb-128

ssh root@pve-lab-1 \
  'journalctl -kf -o short-iso-precise' |
  tee sn770-results/pve-lab-1/731100WD/hmb-128/kernel.log
```

In other observer terminals:

```bash
ssh root@pve-lab-1 \
  'zpool iostat -v sn770test 5' |
  tee sn770-results/pve-lab-1/731100WD/hmb-128/zpool-iostat.log
```

```bash
ssh root@pve-lab-1 \
  'iostat -x 5' |
  tee sn770-results/pve-lab-1/731100WD/hmb-128/iostat.log
```

Each test run gets its own directory containing:

- Firmware and HMB setting.
- Full kernel command line.
- Actual allocated HMB.
- Start/end timestamps.
- Send and scrub counts.
- Bytes read/written.
- Initial and final SMART/error logs.
- Kernel, PCIe, and ZFS events.

## 4A. Isolated 4 KiB logical-sector reproduction arm

This arm was added after the clean 2026-08-20 historical-HMB series. The canonical OpenZFS report used SN770 namespaces formatted with 4096-byte logical sectors, and independent reports observed the same controller-drop signature at 4096 bytes but not at 512 bytes. The completed 512-byte runs remain valid negative results for the HMB-size and descriptor-geometry hypotheses; they did not test this separate namespace-format trigger.

Run this arm only on the disposable SN770 in `pve-lab-1`. Preserve the existing 512-byte evidence off-host before formatting, and keep firmware `731100WD` unchanged until a repeatable failing condition is either established or this arm is classified as another negative reproduction.

Sequence:

1. Confirm no workload is active, `sn770test` is healthy, and the existing evidence has been sealed off-host.
2. Capture the current namespace format with `nvme id-ns -H`, `lsblk -o NAME,MODEL,SERIAL,SIZE,LOG-SEC,PHY-SEC`, and `/sys/block/nvme0n1/queue/logical_block_size`.
3. Export or destroy only the disposable `sn770test` pool, verify it is no longer imported, and repeat the mandatory host/model/serial identity gate immediately before `nvme format`.
4. Confirm that LBA format 1 is 4096 bytes on this exact controller, then run `nvme format /dev/nvme0n1 --lbaf=1 --force`. Re-read Identify Namespace after any controller reset and require a 4096-byte live logical block size.
5. Reboot into `6.8.12-17-pve`, confirm firmware `731100WD`, and require live HMB `HSIZE: 8192` with `HMDLEC: 8`.
6. Recreate the same `ashift=12` pool and 700 GiB incompressible seed described in section 5.
7. Run one send-only screen first, followed by one scrub-only screen and one concurrent send-plus-scrub screen. Use the historical matching userspace, retain the 80 C composite / 103 C maximum-sensor limits established with the proper heatsink, and pass `--logical-block-size 4096` to the bounded runner.
8. Stop immediately on a namespace disappearance, NVMe timeout/reset, `CSTS=0xffffffff`, PCIe/AER failure, SMART/media change, ZFS error, or thermal guard. Preserve evidence before attempting recovery.

Decision rules:

- If 4 KiB reproduces the controller drop, cold-power-cycle and repeat the exact `731100WD`/4 KiB/historical-HMB condition once before changing firmware. A confirmed failure becomes the baseline for a same-drive firmware A/B.
- If all three 4 KiB screens complete cleanly, do not infer that a 512-byte 48-hour dwell will reproduce this trigger. Test a second 1 TB specimen at 4 KiB or a closer multi-drive topology next.
- Return the drives to 512-byte logical sectors for the final operational qualification in section 12. The 4 KiB format is a reproduction condition, not the intended deployment format.

## 5. Create the disposable ZFS workload

Resolve the exact by-id path and manually verify it before continuing:

```bash
ls -l /dev/disk/by-id/ | grep WD_BLACK
```

Then assign only the verified SN770:

```bash
DISK=/dev/disk/by-id/nvme-WD_BLACK_SN770_1TB_EXACT_SERIAL
readlink -f "$DISK"
lsblk -o NAME,MODEL,SERIAL,SIZE "$DISK"
```

Everything below destroys the selected disk:

```bash
wipefs -a "$DISK"
zpool labelclear -f "$DISK" || true

zpool create -f \
  -o ashift=12 \
  -O compression=off \
  -O atime=off \
  -O xattr=sa \
  -m /sn770test \
  sn770test "$DISK"

zfs create -o recordsize=1M sn770test/payload
zfs create -o recordsize=128K sn770test/churn
```

Create a dataset larger than host RAM:

```bash
fio \
  --name=seed \
  --filename=/sn770test/payload/seed.bin \
  --size=700G \
  --rw=write \
  --bs=1M \
  --ioengine=io_uring \
  --iodepth=32 \
  --direct=1 \
  --refill_buffers=1 \
  --end_fsync=1
```

If the installed ZFS version rejects direct file I/O, record that and repeat with `--direct=0`; do not silently change it.

Create the stable send source:

```bash
sync
zfs snapshot sn770test/payload@seed
zpool scrub -w sn770test
zpool status -v sn770test
```

Any checksum or pool error here rejects the drive before HMB testing begins.

## 6. Use one fixed reproducer

Run these concurrently.

Repeated ZFS send:

```bash
while true; do
  date --iso-8601=seconds
  zfs send -v sn770test/payload@seed >/dev/null || exit 1
done
```

Repeated scrub:

```bash
while true; do
  date --iso-8601=seconds
  zpool scrub -w sn770test || exit 1
  zpool status -xv sn770test || exit 1
done
```

For four hours during each 24-hour period, add controlled churn:

```bash
fio \
  --name=churn \
  --directory=/sn770test/churn \
  --size=16G \
  --numjobs=4 \
  --rw=randrw \
  --rwmixread=70 \
  --bs=128K \
  --ioengine=io_uring \
  --iodepth=16 \
  --direct=1 \
  --time_based \
  --runtime=4h \
  --fsync=64 \
  --group_reporting
```

The send/scrub workload is primary because it most closely matches the reported failure. The mixed workload is supplemental and should have a recorded write budget.

Before starting a concurrent or multi-day run, screen the trigger in three separate
arms: send-only, scrub-only, then concurrent send plus scrub. Use the send-only arm
first because it most directly matches the canonical report and may reproduce in
the first tens of gigabytes. A short pass is only a negative reproduction result;
it is not a qualification pass.

If the platform reaches the drive's thermal warning threshold, use
`tools/sn770-zfs-qualification/sn770-send-screen.sh` for bounded `send`, `scrub`, or `mixed` attempts.
Its defaults stop at 80 C composite, 90 C on any individual sensor, or any SMART
critical warning. Pass `--hmb-segments 8` for a historical-geometry arm and
`--zfs-root /opt/zfs-2.2.8-pve1` when using its isolated matching userspace. A
thermal stop is a platform-cooling result, not a controller-drop reproduction.

The 2026-08-20 proper-heatsink historical screens explicitly retained the 80 C
composite guard and passed `--sensor-stop 103`; they did not run under the
general 90 C sensor default. That recorded exception was used only after the
new heatsink eliminated the earlier 103 C plateau, while the SMART critical
warning remained a mandatory stop. The maximum reported channel was Sensor 1,
whose physical location is vendor-specific.

## 7. Test the HMB matrix on 731100WD

Change only `nvme.max_host_mem_size_mb`. Keep firmware, kernel, scheduler, APST, ASPM, PCIe generation, sector size, pool, and workload unchanged.

Test in this order on `pve-lab-1`:

1. `128` — current PVE default.
2. `32` — tests the historical total allocation size on the modern allocator.
3. `200` — requests the controller’s full advertised preference.
4. `0` — disables HMB.

Use the applicable bootloader path:

- GRUB: add the parameter to `GRUB_CMDLINE_LINUX_DEFAULT`, then run `update-grub`.
- A system managed by `proxmox-boot-tool`: update `/etc/kernel/cmdline`, then run `proxmox-boot-tool refresh`.

After every boot, verify rather than assuming:

```bash
cat /proc/cmdline
cat /sys/module/nvme/parameters/max_host_mem_size_mb

journalctl -k -b |
  grep -E 'nvme|host memory buffer'
```

With the modern allocator and a working IOMMU, expected allocations are:

| Parameter | Expected allocation |
|---:|---:|
| `0` | none |
| `32` | 32 MiB, one segment |
| `128` | 128 MiB, one segment |
| `200` | 200 MiB, one segment |

The observed boot log is authoritative.

Total HMB size and descriptor geometry are separate variables. Linux commit
`63a5c7a4b4c49ad86c362e9f555e6f343804ee1d`, merged for 6.13, added a
single-IOVA-segment allocation path. Consequently, a modern 32 MiB / one-segment
run does not reproduce the older 32 MiB / eight-4-MiB-segment behavior reported
with this controller.

If none of the modern-kernel conditions reproduces the failure, boot a separate
disposable USB environment using a 6.12-era kernel. Preserve the current PVE USB.
Confirm the actual HMB allocation geometry before applying workload. Recreate and
reseed a disposable pool under that environment rather than assuming its older
OpenZFS module can import a pool created with current feature flags. Compare:

1. Modern kernel, 128 MiB / one segment.
2. Modern kernel, 32 MiB / one segment.
3. Historical kernel, 32 MiB / multiple segments.

The implemented historical arm uses the signed PVE kernel
`6.8.12-17-pve` alongside the normal PVE 9 kernel on the same disposable USB.
It must be selected explicitly for the test boot; clear GRUB's `next_entry`
immediately after confirming the running kernel. The kernel provides OpenZFS
`2.2.8-pve1`; use the matching checksum-verified userspace extracted at
`/opt/zfs-2.2.8-pve1` rather than the PVE 9 ZFS commands. Before every workload,
require this read-only live check:

```sh
nvme get-feature /dev/nvme0 -f 0x0d -H
```

For the historical condition, `HSIZE` must be `8192` and `HMDLEC` must be `8`.
The runner's `--hmb-segments 8` gate enforces the latter. A SystemRescue 12.03
discovery boot and the PVE 6.8 workload boot both produced that exact result on
SN770 serial `23030W800174`.

Run each discovery condition for at least:

- 48 continuous hours;
- ten complete ZFS sends;
- five complete scrubs.

Use whichever requirement takes longer.

Power the host off and remove AC power for approximately 30 seconds between conditions. This ensures each condition starts with a freshly initialized controller.

If a condition fails, cold-boot and reproduce that same condition once more before changing variables.

## 8. Define a controller failure precisely

Stop a run immediately for any of:

- NVMe command timeout.
- Controller reset.
- `CSTS=0xffffffff`.
- Controller reset failure or `ENODEV`.
- Namespace or capacity disappearance/change.
- PCIe/AER error.
- New SMART media error.
- ZFS pool suspension or permanent checksum error.
- Temperature reaching the controller's reported warning threshold (classify as
  a thermal stop unless accompanied by an independent controller failure).

Before power-cycling, attempt bounded diagnostics:

```bash
timeout 5 nvme list
timeout 5 nvme smart-log /dev/nvme0
timeout 5 cat /sys/class/nvme/nvme0/state
timeout 5 lspci -nnvv -s PCI_BDF
timeout 5 zpool status -v sn770test
timeout 5 zpool events -v
journalctl -k -b --no-pager
```

Record separately whether:

- Commands merely timed out.
- Reset failed.
- CSTS became all ones.
- PCI configuration space remained readable.
- The namespace disappeared.
- ZFS subsequently reported I/O or checksum errors.

Do not collapse those into the generic description “the SSD failed.”

In particular, record SMART temperature warnings separately from NVMe command
timeouts, reset attempts, `CSTS=0xffffffff`, namespace disappearance, or PCIe/AER
events. Cooling must be corrected before a thermal-limited run can count toward
long-duration qualification.

## 9. Upgrade only `pve-lab-1`

After completing the 731100WD matrix:

1. Save all results.
2. Confirm the current firmware offered for the exact 1 TB model.
3. Connect the drive natively to an M.2 slot in a supported Windows environment.
4. Use the official SanDisk Dashboard.
5. Decline any Windows request to initialize or format the disk.
6. Perform a complete power-off after the update.
7. Return the drive to the same host and PCIe slot.
8. Verify the firmware revision and all HMB Identify fields again.

Do not use `nvme fw-download` from the gist for the qualification result; that is not SanDisk’s supported update workflow.

Import and verify the existing pool:

```bash
zpool import
zpool import sn770test
zpool scrub -w sn770test
zpool status -v sn770test
```

If the earlier controller failure left pool errors, recreate the dataset before comparison.

## 10. Perform the firmware A/B comparison

On updated `pve-lab-1`:

1. Repeat the exact HMB condition that failed most reliably on 731100WD.
2. Repeat the default 128 MiB condition.
3. If HMB size appeared correlated with failures, repeat the complete matrix.

During this time, leave `pve-lab-2` on 731100WD and run the same failing condition as a control.

That provides:

- Same host and drive, before versus after firmware.
- An old-firmware drive running concurrently.
- Protection against concluding that a quiet week or environmental change was a firmware fix.

Do not update `pve-lab-2` until this comparison is complete.

## 11. Replicate on the second drive

If `pve-lab-1` improves:

1. Preserve all results.
2. Update `pve-lab-2` through Dashboard.
3. Repeat the previously failing condition.
4. Repeat the production-default 128 MiB condition.

A single drive surviving after a one-way firmware upgrade is not sufficient.

## 12. Final operational qualification

Only after the A/B experiment, update `pve-lab-3` and run all three drives with:

- Current official firmware.
- Current intended PVE kernel.
- Default 128 MiB HMB cap.
- 512-byte logical sectors.
- Default scheduler, APST, ASPM, and PCIe behavior.
- The same send/scrub workload.

Qualification target:

- Seven continuous days per drive, preferably in parallel.
- At least ten full scrubs per drive.
- At least twenty complete sends per drive.
- A controlled mixed-I/O period each day.
- Zero controller resets, timeouts, AER errors, media errors, capacity changes, or permanent ZFS errors.

This gives 21 aggregate drive-days. If the historical failure rate were approximately one failure per drive-week, zero failures over three drive-weeks would be materially inconsistent with an unchanged failure rate, though it still would not prove lifetime safety.

## 13. Decision rules

Classify the result before testing:

- **731150WD fails once with controller loss:** reject SN770 for ZFS.
- **731100WD fails reproducibly and 731150WD survives at least 10× the same time or bytes across two drives:** strong firmware mitigation evidence.
- **Failure follows HMB size on unchanged firmware:** HMB allocation is a trigger or mitigation, not necessarily the underlying defect.
- **Failure occurs at every HMB size:** undersized HMB is unlikely to be the sole cause.
- **No 731100WD condition reproduces the problem:** firmware causality remains unproven; final qualification may support an operational decision but cannot demonstrate a fix.
- **All three current-firmware drives pass final qualification:** sufficient evidence to reconsider ZFS, with the result documented as a platform-specific qualification—not a universal SN770 safety claim.
- **Any inconclusive result:** retain ext4/LVM-thin.

The 200 MiB override remains diagnostic. Even if it performs best, I would not make it a production dependency without independent evidence that default 128 MiB is unsafe and 200 MiB is consistently stable.

This procedure is substantially different from the generic PVE burn-in section
and is the authoritative qualification procedure for this experiment.

## Lab-host state after the 2026-08 campaign

- `pve-lab-1` boots an expendable PVE install from a USB stick; the host has no SATA disk, and the NVMe under test stays raw with no OS, mount, or swap on it.
- BIOS deltas applied from factory defaults: VT-x and VT-d on, After Power Loss = Power On, Fast Boot off, Network (PXE) Boot on, Secure Boot off. Boot order is USB, then NVMe, then network; PXE is reached from the Startup Menu (hold Esc through POST, then F12 → IPv4).
- Kernel ↔ HMB geometry on this host: the stock PVE kernel allocates a 128 MiB HMB in one descriptor; the installed non-default `6.8.12-17-pve` kernel allocates 32 MiB across eight descriptors — the historical allocator geometry the reproduction arms require.
- The bounded runner is installed on-host as `/usr/local/sbin/sn770-send-screen.sh` from its Git source in [`tools/sn770-zfs-qualification/`](../../tools/sn770-zfs-qualification/).
- A hard hang needs a physical power-cycle; the KVM has no ATX wiring to this node.
