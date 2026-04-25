# Storage Benchmarks

Reproducible Longhorn benchmarks against the prod cluster, with the headline numbers and what they mean. Run via `tools/longhorn-bench/` (see [Reproducing](#reproducing) below).

## Setup

| | |
|---|---|
| Date run | 2026-04-25 |
| Cluster | k8s-prod (3× HP EliteDesk 705 G4, Ryzen 5 2400GE, 64 GB RAM) |
| Disk | WD_BLACK SN770 1 TB NVMe, dedicated user volume on `/var/mnt/longhorn` (~828 GiB, xfs) |
| Storage VLAN | 2.5 GbE node ↔ node, validated at line rate (2.35 Gbit/s) with zero retransmits |
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

**Caveat on locality:** The default `longhorn` StorageClass shipped by the chart hardcodes `dataLocality: "disabled"` in its parameters, which overrides the global `defaultDataLocality: best-effort` we set in the HelmRelease. So in these runs reads were not guaranteed to come from the local replica — the engine's leader replica served them. A future benchmark with an explicit `dataLocality: best-effort` StorageClass would isolate the local-read benefit.

## Results — 2-replica vs 3-replica

Both runs: bench pod on `k8s-prod-3`, default reclaim, `dataLocality: disabled`.

| Profile | 2-replica | 3-replica | Δ |
|---|---|---|---|
| Seq write 1M | 113 MB/s · 107 IOPS · 296 ms queue | 56 MB/s · 54 IOPS · 596 ms queue | **−50 %** |
| Seq read 1M | 233 MB/s · 222 IOPS | 176 MB/s · 168 IOPS | −24 % |
| Random read 4K | 13.3 K IOPS · 2.4 ms | 13.3 K IOPS · 2.4 ms | **0 %** |
| Random write 4K | 13.1 K IOPS · 2.4 ms | 11.0 K IOPS · 2.9 ms | −16 % |
| Mixed 70/30 4K | 9.2 K read + 4.0 K write IOPS | 8.4 K read + 3.6 K write IOPS | −9 % |

## Key findings

**1. Single-stream throughput is latency-bound at the Longhorn engine, not network-bound.**
The storage VLAN can carry 2.35 Gbit/s (~280 MB/s), but we only saw 113 MB/s sequential write at 2-rep — well under the network ceiling. The bottleneck is the engine's per-op cost (iSCSI client → loopback → engine pod → fan out to replicas → ACK chain), not bandwidth. To confirm: at 2.4 ms per random 4K op, depth 32 yields ~13 K IOPS regardless of replica count for reads. The engine has a fixed overhead floor.

**2. Going 2 → 3 replicas exactly halves sequential write throughput.**
Per-op latency on 1 MB writes doubled (296 ms → 596 ms queue, ~9 ms → ~18 ms per op). The engine appears to serialize remote stream submission rather than truly parallelize, so each extra remote replica adds another full network round-trip per block. This is the steepest cost we observed.

**3. Small random IO is much more resilient to extra replicas.**
4K random write IOPS dropped only 16 % (13.1 K → 11.0 K) and per-op latency rose only 20 % (2.4 → 2.9 ms). At small block sizes, the engine keeps both remote replica writes "in flight" in parallel, so queue depth largely hides the extra-replica cost. Useful for OLTP workloads.

**4. Random read IOPS unchanged across replica counts.**
Reads are gated by the engine's per-op latency floor; replica count doesn't matter because reads only touch one replica. A nice property — the read side of any workload pays no penalty for higher durability.

**5. The replication tax is workload-shaped.**
Sequential write doubles in cost. Random IO barely changes. A backup tool will feel the 3-replica hit hard; a Postgres OLTP load will barely notice.

## Implications for replica-count choices

This data backs up the architecture doc's strategy: **2-replica default, 3-replica only for critical PVCs**.

- **2-rep is the right default** for most workloads. It gives 2× the sequential write throughput of 3-rep, which matters for backup staging, image churn, log writers. Recovery from full data loss is via NFS/Velero backup anyway, so the durability difference between 2-rep and 3-rep is "tolerate one host failure" vs. "tolerate two simultaneous host failures" — a low-probability incremental gain in a 3-node homelab.
- **3-rep specifically for stateful databases** (Postgres, Redis with persistence, etcd-equivalents) where the workload is small random IO and the marginal IOPS hit (16 %) is worth the durability bump.
- **Avoid 3-rep for**: bulk media ingest, backup landing zones, anything write-heavy at large block sizes.

## Real-world translation

| Workload | Verdict |
|---|---|
| Postgres / MySQL WAL & data | **Plenty.** 11–13 K IOPS at 2.4–2.9 ms is solid OLTP territory for homelab scale. 3-rep ok. |
| Jellyfin / media streaming | **Plenty.** 4K Blu-ray ≈ 50 Mbit/s; we have 1.4–1.8 Gbit/s read headroom even on 3-rep. 2-rep fine — bulk media doesn't need 3-rep durability. |
| Bulk restore / image pulls | **Slow on 3-rep.** 56 MB/s sequential write means a 100 GB restore takes ~30 min. Use 2-rep or NFS for these. |
| Backup landing | **Use NFS, not Longhorn.** Sequential write throughput is the wrong knife for the job. |
| ML training data shuffling | **Run multi-stream.** Single-stream throughput is per-pipeline limited; multiple PVCs/pods scale linearly. |

## Things to test next time

- **Effect of `dataLocality: best-effort`** — set on a custom StorageClass, re-run. Should boost random read IOPS noticeably (no engine round-trip when replica is local).
- **Multi-stream scaling** — `fio --numjobs=4` or `--numjobs=8`. Single-stream is artificially constrained; multi-stream will saturate the network ceiling we expected.
- **Larger iodepths** (64, 128) — at what depth does Longhorn plateau?
- **V2 data engine** — Longhorn 1.11 ships SPDK-based V2 with significantly better performance characteristics. Requires hugepages + NVMe-oF setup; worth a separate evaluation.
- **Local NVMe baseline** — same fio profiles via a `hostPath` pod, to measure "Longhorn overhead vs raw NVMe" rather than just relative comparisons.

## Reproducing

```bash
# Apply (against the default longhorn 2-rep StorageClass)
kubectl apply -f tools/longhorn-bench/pvc.yaml -f tools/longhorn-bench/fio-job.yaml

# Watch live
kubectl -n default logs -f pod/longhorn-bench

# Tear down (Delete reclaim policy auto-removes the underlying volume)
kubectl delete -f tools/longhorn-bench/fio-job.yaml -f tools/longhorn-bench/pvc.yaml
```

For 3-replica or other replica counts, create a temporary StorageClass and edit `tools/longhorn-bench/pvc.yaml` to reference it. The fio profiles themselves don't need to change.

Both manifests are intentionally **not** Flux-managed — they're operational utilities, applied/torn down by hand.
