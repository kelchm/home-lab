# WD_BLACK SN770 1 TB ZFS qualification record

**Status:** Reproduction work complete on one specimen as of 2026-08-22; multi-specimen operational qualification remains open.

## Executive result

One WD_BLACK SN770 1 TB on firmware `731100WD` completed every bounded reproduction arm without the reported NVMe controller drop. The test matrix covered the current Linux HMB allocator, the historical 32 MiB/eight-descriptor allocator, 512-byte and 4096-byte logical-sector formats, send-only, scrub-only, and concurrent send-plus-scrub workloads.

Across all completed arms, the specimen performed 18 full 700 GiB `zfs send` operations and 8 full 700 GiB scrubs. That represents at least 17.8 TiB of logical reads in completed sends and scrubs, excluding seed writes, partial thermal screens, metadata, and repeated monitoring reads. No arm produced an NVMe timeout or reset, `CSTS=0xffffffff`, namespace disappearance or capacity change, PCIe/AER error, SMART critical warning during a completed arm, media/error-log increment, ZFS data error, or pool degradation.

This is a strong negative reproduction result for this specimen and host. It does not prove that the SN770 model is safe under ZFS, does not measure the public reports' prevalence, and is not a firmware A/B. The public reports include different capacities, controller specimens, host platforms, power-state transitions, and multi-drive topologies. The launch decision remains installer-default ext4 plus LVM-thin for the planned PVE cluster.

## Why this was tested

The [canonical OpenZFS report](https://github.com/openzfs/zfs/discussions/14793) describes three 2 TB SN770s on firmware `731100WD`, formatted with 4096-byte logical sectors in RAIDZ1. Local `zfs send` usually caused a controller to disappear after 12–93 GB with `CSTS=0xffffffff`; ordinary scrubs were normally successful. The same thread accumulated reports involving other capacities, filesystems, NVMe models, platforms, idle states, and power transitions, so it is evidence of real failures rather than a controlled incidence-rate study.

Two reports provide particularly useful namespace-format A/B evidence. A [2023 SanDisk forum report](https://forums.sandisk.com/t/sn770-nvme-controller-reset-when-formatted-with-4096-byte-sectors/357660) describes two SN770s that worked with 512-byte logical sectors, failed on two systems after being reformatted to 4096 bytes, and worked again after returning to 512 bytes. A [2026 OpenZFS follow-up](https://github.com/openzfs/zfs/discussions/14793#discussioncomment-17436255) reports that `zfs send` and scrub reliably reproduced the controller drop at 4096 bytes and stopped reproducing after returning to 512 bytes.

The HMB hypothesis came from an earlier [Linux NVMe allocator investigation](https://www.spinics.net/lists/kernel/msg4339024.html). Older kernels commonly allocated only 32 MiB to the SN770 as eight 4 MiB descriptors. Increasing the allocation improved one reporter's failure frequency but did not eliminate failures, and the original OpenZFS reporter said a larger allocation made the failure occur sooner. Linux later added a [single IOVA-contiguous HMB allocation path](https://github.com/torvalds/linux/commit/63a5c7a4b4c49ad86c362e9f555e6f343804ee1d), which explains the one-descriptor geometry observed on the current PVE kernel.

SanDisk's [published HMB firmware advisory](https://support-en.sandisk.com/app/answers/detailweb/a_id/51469) covers Windows 11 24H2, the 2 TB SN770, and firmware `731130WD`. Firmware `731150WD` is available for the 1 TB model, but no authoritative public changelog was found tying it to this Linux/ZFS failure. It must not be described as a vendor-confirmed Linux fix.

## Test object and controls

The public record intentionally omits hardware serial numbers, MAC addresses, internal IP addresses, temporary credentials, and the private evidence-host location. Each destructive action and workload was gated against privately recorded DMI and NVMe serials.

| Item | Qualification condition |
|---|---|
| Drive | One WD_BLACK SN770 1 TB specimen, firmware `731100WD` |
| Host | HP EliteDesk 800 G3 DM, Core i5-6500T, 32 GB RAM, native PCIe 3.0 M.2 slot |
| Cooling | Proper NVMe heatsink for all complete scrub and mixed historical/4 KiB runs |
| OS isolation | PVE installed on a separate expendable USB drive; the SN770 was a disposable raw test device |
| Current kernel arm | PVE kernel `7.0.14-12-pve`; HMB 128 MiB/one descriptor by default |
| Historical kernel arm | PVE kernel `6.8.12-17-pve`, OpenZFS `2.2.8-pve1`; HMB 32 MiB/eight descriptors |
| Pool | Single whole-disk pool `sn770test`, `ashift=12`, compression off, atime off |
| Dataset | 700 GiB incompressible seed, 1 MiB records, stable `payload@seed` snapshot |
| Workloads | `zfs send` to `/dev/null`, `zpool scrub -w`, and concurrent send plus scrub |
| Thermal guards | Stop at 80 C composite, 103 C maximum sensor, or any SMART critical warning |
| Failure guards | Stop on timeout/reset, controller-down/CSTS signature, namespace loss, PCIe/AER error, SMART/media change, ZFS error, or unhealthy pool |
| Observation | Kernel, SMART, NVMe error log, HMB state, namespace state, thermal samples, pool state, and workload outcome retained locally and streamed off-host where applicable |

## Complete test matrix

### Current-kernel HMB-size screens at 512-byte LBA

These arms isolated total HMB size on the current one-descriptor allocator. All five full sends completed cleanly.

| HMB condition | Workload | Duration / peak | Result |
|---|---|---|---|
| 128 MiB / one descriptor | Two full sends | 5m28s and 5m26s; 78/101 C | Clean |
| 32 MiB / one descriptor | One full send | 5m26s; 78/101 C | Clean |
| 200 MiB / one descriptor | One full send | 5m28s; 78/101 C | Clean |
| Disabled | One full send | 5m30s; 77/100 C | Clean |
| 128 MiB / one descriptor | Initial scrub | Stopped at 31.79%; 84/107 C | Thermal event: SMART warning bit `0x2`; controller and namespace remained present; pool stayed ONLINE; no media/NVMe error |
| 128 MiB / one descriptor | First bounded scrub harness attempt | Canceled at 1.90% | Harness error: the scrub was mistakenly outside monitoring; no drive or pool error |
| 128 MiB / one descriptor | Corrected bounded scrub | Stopped around 40–45%; 80/103 C | Thermal-guard stop; no SMART warning or drive/pool error |

The modern screens show that HMB total size from zero through 200 MiB was not sufficient to trigger the fast send failure on this specimen. The scrub stops identified inadequate cooling, not controller instability.

### Historical HMB geometry at 512-byte LBA

A read-only discovery boot first proved that an older allocator produced the actual target state: `HSIZE=8192` (32 MiB) and `HMDLEC=8`. The signed historical PVE kernel then reproduced that geometry with a matching OpenZFS userspace.

| HMB condition | Workload | Count / duration / peak | Result |
|---|---|---|---|
| 32 MiB / eight descriptors | Send-only before the final heatsink | One full send, 5m48s; 76/99 C | Clean |
| 32 MiB / eight descriptors | Scrub-only before the final heatsink | About 200 GiB issued in 73s; 80/103 C | Thermal-guard stop; pool ONLINE and error-free |
| 32 MiB / eight descriptors | Mixed before the final heatsink | 69s; 80/103 C | Thermal-guard stop; send terminated and scrub canceled cleanly |
| 32 MiB / eight descriptors | Scrub-only with the proper heatsink | One full scrub, 3m41s; 76/95 C | Clean; repaired `0B` |
| 32 MiB / eight descriptors | Mixed with the proper heatsink | Five complete send-plus-scrub cycles, about 7m40s each; 74/94 C | Clean; every scrub repaired `0B` |
| 32 MiB / eight descriptors | Send-only with the proper heatsink | Five full sends, 5m46s–5m47s; 70/88 C | Clean |

This phase completed 11 sends and 6 scrubs in total: one early full send plus the cooled series of 10 sends and 6 scrubs. It directly demonstrates that the historical eight-descriptor HMB geometry is not sufficient to trigger the controller drop on this specimen.

### Isolated 4096-byte LBA arm

The existing pool was intentionally destroyed after sealing the 512-byte evidence. The namespace was reformatted to 4096-byte logical sectors, the serial-gated controller was revalidated after reset, and the same `ashift=12` pool and 700 GiB incompressible dataset were recreated. The new seed completed in 17m02s at 701 MiB/s average without error. No unmonitored setup scrub was performed, preserving send-only as the first reproduction workload.

All three workload arms used firmware `731100WD`, kernel `6.8.12-17-pve`, OpenZFS `2.2.8-pve1`, 4096-byte logical sectors, and HMB 32 MiB/eight descriptors.

| Workload | Duration / peak | Result |
|---|---|---|
| One full send | 5m47s; 68/87 C | Clean |
| One full scrub | 3m42s; 74/94 C | Clean; repaired `0B` |
| One concurrent send plus scrub | 7m38s; 74/94 C | Clean; repaired `0B` |

The send sustained about 2.03 GiB/s and passed the canonical report's 12–93 GB failure window in under a minute. The mixed arm also passed that range while send and scrub competed for the device. Complete local and off-host kernel logs contain no transient target-failure signature.

This phase demonstrates that 4096-byte logical sectors plus the historical HMB geometry and ZFS workloads are not sufficient on this 1 TB specimen and single-drive PCIe 3.0 host.

## Aggregate accounting

Only completed full operations are included here. Thermal stops and the
harness-error run are excluded from the counts but retained in the matrix.

| Phase | Full sends | Full scrubs | Target failures |
|---|---:|---:|---:|
| Current-kernel HMB-size screens, 512-byte LBA | 5 | 0 | 0 |
| Historical eight-descriptor arm, 512-byte LBA | 11 | 6 | 0 |
| Historical eight-descriptor arm, 4096-byte LBA | 2 | 2 | 0 |
| **Total** | **18** | **8** | **0** |

## Findings

### Established by this experiment

- HMB configuration alone was not a trigger on the current kernel: the 0 MiB (disabled), 32 MiB, 128 MiB, and 200 MiB send screens all passed; the enabled cases each used one descriptor.
- The historical 32 MiB/eight-descriptor HMB geometry was genuinely reproduced and is not sufficient by itself on this specimen.
- Changing this specimen from 512-byte to 4096-byte logical sectors did not reproduce the fast ZFS-send failure, even though controlled public A/B reports show that the format matters on other specimens and platforms.
- `zfs send`, scrub, and concurrent send-plus-scrub are not universal detonators for every 1 TB SN770 on `731100WD`.
- The early stopped runs were thermal or harness outcomes, not ambiguous drive failures. A proper heatsink converted the same scrub and mixed workloads into repeatable full passes without a rising heat-soak trend.
- The current public firmware evidence does not support claiming that `731150WD` fixes this Linux/ZFS behavior on the 1 TB model.

### Not established

- The experiment does not establish an SN770 population failure rate or prove lifetime safety.
- It does not isolate capacity, manufacturing lot, controller/NAND revision, PCIe generation, queue concurrency, multi-drive topology, host firmware, or host power delivery.
- It does not test a second 1 TB specimen, the canonical three-drive 2 TB RAIDZ1 topology, or a high-queue PCIe 4.0 platform.
- Continuous heavy I/O does not exercise the separate idle/APST/suspend failure mode described in some 1 TB laptop reports.
- No controller failure was reproduced, so updating this specimen would not produce a meaningful firmware before/after comparison.
- The bounded reproduction series is not the planned 48-hour operational dwell or seven-day/three-drive final qualification.

## Decision and next tests

The PVE launch design remains ext4 plus LVM-thin. A clean result on one specimen reduces concern for that exact combination but does not erase credible controller-drop reports or justify placing the only virtualization-host disk behind a storage stack whose routine integrity workload appears in those reports.

Next tests, in information-gain order:

1. Inventory a second 1 TB SN770 on `731100WD`, format that disposable specimen to 4096-byte logical sectors, and test it on the same host. This isolates specimen/lot variation with the fewest changed variables.
2. If a second specimen also passes, reproduce a closer topology: multiple old-firmware SN770s, 4096-byte logical sectors, RAIDZ or mirror concurrency, and a higher-queue PCIe 4.0 host.
3. Treat a 48-hour 512-byte dwell as operational qualification and an idle/APST-transition screen, not as the primary way to hunt the canonical fast 4 KiB failure.
4. Do not update the old-firmware specimen until a failing baseline exists. If one is established, use the official Windows SanDisk Dashboard and rerun the identical failing condition before and after firmware while holding another drive on old firmware.
5. Return every candidate drive to 512-byte logical sectors before the final seven-day, three-drive operational qualification.

## Evidence retention

Raw logs, SMART/NVMe captures, run manifests, and checksums are retained
privately outside Git. This public record contains only the sanitized method and
result summary; unique hardware identifiers and internal evidence locations are
intentionally omitted.
