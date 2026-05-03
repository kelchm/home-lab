# Storage Benchmarks

Reproducible Longhorn benchmarks against the prod cluster, with the headline numbers and what they mean. Run via `tools/longhorn-bench/` (see [Reproducing](#reproducing) below).

Two benchmark generations are recorded here:

- **2026-04-25**: pre-cutover — Longhorn replica engine ↔ replica engine traffic was riding the 1 GbE pod network on VLAN 30 (we believed it was on VLAN 25 at the time, but it never was — discovered later during backup planning).
- **2026-05-03**: post-cutover — replica traffic now rides VLAN 25 (2.5 GbE storage NIC) via Multus + bridge CNI on a per-node Linux bridge. Bench was run on all three prod nodes for cross-node confirmation.

The 2026-05-03 numbers correct several conclusions from the 2026-04-25 run.

## TL;DR — pre vs post cutover (2-replica, three-node average)

| Profile | Pre-cutover (1 GbE) | Post-cutover (2.5 GbE bridge, avg) | Δ |
|---|---|---|---|
| **Seq write 1M** | 113 MB/s | **201 MB/s** | **+77 %** |
| **Seq read 1M** | 233 MB/s | **434 MB/s** | **+86 %** |
| **Random read 4K** | 13.3 K IOPS | 12.6 K IOPS | -5 % |
| **Random write 4K** | 13.1 K IOPS | 11.7 K IOPS | **-11 %** |
| **Mixed 70/30 4K** | 13.2 K IOPS total | 12.3 K IOPS total | -7 % |

**One-sentence takeaway:** sequential workloads were 1 GbE-bound and ~doubled; small random IO was engine-bound and is now *slightly worse* (~10 %) because bridge CNI's per-packet kernel cost shows up on synchronous fan-out writes. Net trade is great for backup/restore-shaped IO, mildly negative for OLTP-shaped IO.

Per-node breakdown and full discussion below.

## Setup

| | |
|---|---|
| Cluster | k8s-prod (3× HP EliteDesk 705 G4, Ryzen 5 2400GE, 64 GB RAM) |
| Disk | WD_BLACK SN770 1 TB NVMe, dedicated user volume on `/var/mnt/longhorn` (~828 GiB, xfs) |
| Storage VLAN link | 2.5 GbE node ↔ node, validated at line rate (2.35 Gbit/s) with zero retransmits |
| Replica network path | 2026-04-25: VLAN 30 (1 GbE, Cilium pod network). 2026-05-03: VLAN 25 (2.5 GbE bridge CNI, host + pods on `br-storage`). |
| Longhorn | v1.11.1, V1 data engine |
| Bench tool | fio 3.13, single job, `iodepth=32`, `--direct=1`, `--ioengine=libaio` |

## Methodology

Five fio profiles cover the interesting axes of storage performance:

1. **Sequential write 1M** — write the full 10 GB test file (no `--time_based`). Measures bulk write throughput.
2. **Sequential read 1M** — 30 s `--time_based`. Measures bulk read throughput.
3. **Random read 4K** — 30 s. Measures small-IO read IOPS / latency.
4. **Random write 4K** — 30 s. Measures small-IO write IOPS / latency under sync replication.
5. **Random read/write 70/30 mix at 4K** — 30 s. DB-ish workload approximation.

`--direct=1` bypasses the page cache so we measure the storage path, not OS buffering. `--norandommap` allows fio to revisit blocks (still random, just no internal tracking overhead at this depth).

**Caveat on locality:** The default `longhorn` StorageClass shipped by the chart hardcodes `dataLocality: "disabled"` in its parameters, which overrides the global `defaultDataLocality: best-effort` we set in the HelmRelease. Reads are not guaranteed to come from the local replica — the engine's leader replica serves them, which may or may not be local. A future benchmark with an explicit `dataLocality: best-effort` StorageClass would isolate the local-read benefit.

## Results — pre vs post cutover (2-replica)

Same 5 profiles, 2-replica StorageClass, default reclaim, `dataLocality: disabled`.

| Profile | Pre-cutover (1 GbE), prod-3 | Post (2.5 GbE bridge), prod-1 | prod-2 | prod-3 | Avg Δ |
|---|---|---|---|---|---|
| **Seq write 1M** | 113 MB/s · 107 IOPS | **206 MB/s** (+82 %) | 198 (+75 %) | 198 (+75 %) | **+77 %** |
| **Seq read 1M** | 233 MB/s · 222 IOPS | **416 MB/s** (+78 %) | 430 (+85 %) | 455 (+95 %) | **+86 %** |
| **Random read 4K** | 13.3 K IOPS | 12.4 K (-7 %) | 12.2 (-8 %) | 13.1 (-1 %) | **-5 %** |
| **Random write 4K** | 13.1 K IOPS | 11.2 K (-15 %) | 11.6 (-11 %) | 12.2 (-7 %) | **-11 %** |
| **Mixed 70/30 4K** | 9.2 K + 4.0 K = 13.2 K total | 8.3 + 3.6 = 11.9 K (-10 %) | 8.6 + 3.7 = 12.3 (-7 %) | 8.9 + 3.8 = 12.8 (-3 %) | **-7 %** |

Cross-node band on the post-cutover run: tight enough that the trade is structural, not node-specific. Replica-host load matters slightly — prod-3 (4 replicas hosted) consistently beats prod-1 (6 replicas hosted) by 5–8 % on small IO; sequential is essentially identical across nodes.

## Reference data — replica-count cost (pre-cutover, 1 GbE)

From the 2026-04-25 run on prod-3, both replica counts on 1 GbE so the absolute numbers are stale but the **ratios** between 2-rep and 3-rep are still the cleanest data we have on per-replica cost. A re-run on the post-cutover network is pending.

| Profile | 2-replica | 3-replica | Δ |
|---|---|---|---|
| Seq write 1M | 113 MB/s · 296 ms queue | 56 MB/s · 596 ms queue | **−50 %** |
| Seq read 1M | 233 MB/s | 176 MB/s | −24 % |
| Random read 4K | 13.3 K IOPS | 13.3 K IOPS | **0 %** |
| Random write 4K | 13.1 K IOPS · 2.4 ms | 11.0 K IOPS · 2.9 ms | −16 % |
| Mixed 70/30 4K | 9.2 K + 4.0 K | 8.4 K + 3.6 K | −9 % |

## Key findings

**1. The pre-cutover "engine-latency-bound, not network-bound" conclusion was wrong for sequential workloads.**
The 2026-04-25 doc claimed sequential IO was engine-bound. The 113 MB/s sequential write was suspiciously close to 1 GbE line rate (~125 MB/s) but we explained it away as engine overhead. The post-cutover bench refuted this: same engine, same iSCSI path, only the network changed — and sequential write nearly doubled to 198 MB/s. **Sequential 1 MB workloads were network-bound on 1 GbE the whole time.** Reads same story (233 → ~430 MB/s).

**2. Small random 4K IO IS engine-bound, not network-bound.**
4K random read IOPS barely moved (13.3 → ~12.6 K). At 4K with depth 32, per-op overhead in the engine dominates the wall-clock time of each op; network latency is a small contributor. Same conclusion the 2026-04-25 doc reached for this profile, just narrower in scope than originally written.

**3. Bridge CNI introduces a small per-packet cost on small synchronous writes.**
Random write 4K is the only profile that got *slower* post-cutover, by 7–15 % across nodes. Pattern is consistent enough across three nodes to be structural, not noise. Bridge CNI puts pod traffic through a Linux bridge in the host kernel; Cilium's BPF datapath is more direct. Synchronous fan-out writes to two replicas pay the per-packet cost twice. ~10 % is the practical headline number.

**4. Sequential reads cross the network too — locality is not in effect.**
The chart's default `longhorn` StorageClass forces `dataLocality: disabled`, so reads go to the engine's leader replica regardless of where it lives. That's why sequential reads also nearly doubled (network-bound) rather than holding flat (which they would if reads were always local). A `dataLocality: best-effort` StorageClass would change this — local reads should be at NVMe speed and unaffected by network changes.

**5. The 2 → 3 replica cost is workload-shaped (still valid from the 2026-04-25 run).**
Sequential write doubles in cost. Random IO barely changes. The mechanism the engine uses (apparently serializing remote-stream submission for sequential, parallelizing for small random) is independent of whether the network is 1 GbE or 2.5 GbE. Re-running 3-replica on the post-cutover network would reset the absolute numbers but the percentage hit should hold.

**6. Replica-host load matters slightly.**
prod-3 (4 replicas hosted) consistently beat prod-1 (6 replicas hosted) by 5–8 % on small-IO post-cutover. Sequential is unaffected by host load (probably because the bottleneck is bandwidth on the wire, which doesn't depend on what other replicas live on the host).

## Implications for replica-count choices

The 2-replica default still holds, with a slightly different rationale than the 2026-04-25 doc gave:

- **2-rep is the right default** for most workloads. Sequential write throughput is ~2× 3-rep, which matters for backup staging, image churn, log writers. Recovery from full data loss is via NFS/Velero backup anyway, so the durability difference between 2-rep and 3-rep is "tolerate one host failure" vs. "tolerate two simultaneous host failures" — a low-probability incremental gain in a 3-node homelab.
- **3-rep specifically for stateful databases** (Postgres, Redis with persistence) where the workload is small random IO and the marginal IOPS hit is small (~16 % on 1 GbE; should be similar percentage post-cutover).
- **Avoid 3-rep for**: bulk media ingest, backup landing zones, anything write-heavy at large block sizes.

## Real-world translation

Updated for post-cutover 2-replica numbers (averaged across three nodes):

| Workload | Verdict |
|---|---|
| Postgres / MySQL WAL & data | **Plenty.** ~11.7 K random write IOPS at 2-rep still solid OLTP territory for homelab scale, even with the bridge tax. 3-rep should still work. |
| Jellyfin / media streaming | **Comfortable.** 4 K Blu-ray ≈ 50 Mbit/s; we have ~3.4 Gbit/s of sequential read headroom now. |
| Bulk restore / image pulls | **Substantially better.** 198 MB/s sequential write @ 2-rep means a 100 GB restore takes ~9 min (was ~15 min on 1 GbE @ 2-rep, ~30 min @ 3-rep). |
| Backup landing | **Longhorn is now viable** for backup-shaped writes — no longer the wrong knife for the job. NFS to Synology (per architecture-doc storage strategy) is still cleaner for the backup MVP because it sidesteps the 2× write amplification of replication, but Longhorn isn't the bottleneck it was. |
| ML training data shuffling | **Run multi-stream.** Single-stream throughput is per-pipeline limited; multiple PVCs/pods scale linearly. |

## Things to test next time

- **3-replica re-run on the post-cutover network** — confirms the replica-count ratios from the 2026-04-25 reference data hold and gives us absolute numbers to compare against the 2-rep post-cutover run.
- **`dataLocality: best-effort`** — set on a custom StorageClass, re-run profiles 2 and 3. Should boost random read IOPS and sequential read MB/s when the local replica is the leader (no network round-trip).
- **Multi-stream scaling** — `fio --numjobs=4` or `--numjobs=8`. Single-stream is artificially constrained; multi-stream should saturate the 2.5 GbE ceiling for sequential and may surface different scaling for small IO.
- **Larger iodepths** (64, 128) — at what depth does Longhorn plateau?
- **V2 data engine** — Longhorn 1.11 ships SPDK-based V2 with significantly better performance characteristics. Requires hugepages + NVMe-oF setup; worth a separate evaluation.
- **Local NVMe baseline** — same fio profiles via a `hostPath` pod, to measure "Longhorn overhead vs raw NVMe" rather than just relative comparisons.
- **Quantify the bridge CNI per-packet cost more directly** — e.g., a TCP_RR netperf between IM pods over the bridge vs. the same path on the Cilium pod network. Would isolate "what does bridge cost in microseconds" from "what is the engine fixed-overhead floor."

## Reproducing

```bash
# Apply (against the default longhorn 2-rep StorageClass)
kubectl apply -f tools/longhorn-bench/pvc.yaml -f tools/longhorn-bench/fio-job.yaml

# Watch live
kubectl -n default logs -f pod/longhorn-bench

# Tear down (Delete reclaim policy auto-removes the underlying volume)
kubectl delete -f tools/longhorn-bench/fio-job.yaml -f tools/longhorn-bench/pvc.yaml
```

The pod is pinned via `nodeName: k8s-prod-3` so successive runs land on the same NVMe, CPU, and NIC silicon as the original baseline. Override at apply time to compare other nodes:

```bash
sed 's|nodeName: k8s-prod-3|nodeName: k8s-prod-1|' tools/longhorn-bench/fio-job.yaml | kubectl apply -f -
```

For 3-replica or other replica counts, create a temporary StorageClass and edit `tools/longhorn-bench/pvc.yaml` to reference it. The fio profiles themselves don't need to change.

Both manifests are intentionally **not** Flux-managed — they're operational utilities, applied/torn down by hand.
