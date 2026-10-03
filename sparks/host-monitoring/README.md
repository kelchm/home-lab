# Spark host monitoring

Each Spark runs an independent monitoring Compose project managed by its own doco-cd instance. node-exporter, the GPU exporter and vmagent listen on loopback. vmagent pushes to the [authenticated ingestion endpoint](../../docs/runbooks/external-metrics.md) with a host-specific credential and a persistent disk queue. SparkRun owns inference; this project has no dependency on its containers and never restarts them.

Live enrollment and sustained-load acceptance are tracked in [#697](https://github.com/kelchm/home-lab/issues/697). The code and isolated qualification do not establish permanent collection. Ingestion [#694](https://github.com/kelchm/home-lab/issues/694) must be accepted before cutover. Keep the workstation TensorFold pilot until the Spark 1 collector is verified, then stop it to avoid duplicate samples.

## Components and cadence

| Job | Interval | Coverage |
|---|---|---|
| `spark-node` | 15 seconds | CPU, unified memory, filesystem/disk, thermal sensors, pressure, Ethernet carrier/error counters, endpoint InfiniBand/RDMA counters, deployment textfiles |
| `spark-gpu` | 5 seconds | GB10 utilization, temperature, power, SM clocks, thermal-throttle flags/counters, recovery action and exporter success/freshness |
| `spark-collector` | 15 seconds | vmagent process, scrape, config-reload and queue/delivery health |
| `spark-deployer` | 15 seconds | doco-cd process, successful polls and deployment failures |
| `spark-inference` | 1 second, Spark 1 only | Native TensorFold and TensorFold-health families for the single TP=2 serving deployment |

The GPU exporter uses its ARM64 `exec` backend and NVIDIA CDI device injection. The NVML image is AMD64-only at the qualified version. GB10 reports framebuffer memory as unavailable, and memory utilization is misleading for its unified memory: use `node_memory_MemAvailable_bytes` and pressure instead. A power-cap event alone is ordinary operation; thermal throttling and memory pressure are separate signals. Do not collect per-process GPU metrics or add process-ID labels.

Images are pinned by digest. The runtime paths are `/var/lib/spark-monitoring/{config,queue,textfile}` and `/etc/spark-monitoring/credentials/token`. They are independent of doco-cd's per-commit Git exports. Every long-running service uses host networking so it can reach host loopback, runs as UID/GID 65534 with a read-only filesystem and no added capabilities, and has CPU/memory limits. node-exporter additionally reads the host root and PID namespace; the GPU exporter receives the utility driver/device access required by `nvidia-smi`.

## Configuration delivery and failures

The one-shot publisher alone mounts a Git file. It validates the candidate with the same pinned vmagent image before atomically replacing the applied file. vmagent checks that file every five seconds. Invalid candidates retain a validated applied file and publish `spark_monitoring_config_success=0` plus distinct desired/applied SHA-256 labels. If no valid applied file exists, initial bootstrap fails. Read the publisher log and fix or revert the candidate; the rejected desired commit is not the applied scrape configuration.

That distinction is deliberate: doco-cd 0.123.0 force-recreates a project on recovery from a failed deployment. Returning a failed publisher job for an invalid candidate can turn a later retry into a collector outage. Retaining a valid applied file lets the monitoring project continue while its configuration failure is visible. Image, Compose and runtime failures can still partially change the project; there is no transaction across containers. Persistent queues survive collector replacement.

Each deployer polls `main` every three minutes and targets only its own host directory. The deployer definition is applied manually and is not one of its targets. Its metrics port 9120 is published on host loopback only; local vmagent scrapes it to detect stopped polling and deployment errors. Unrelated commits leave the project's containers unchanged. Reconciliation is disabled: Git changes are applied, while ad hoc runtime changes are not continuously repaired. Anyone who can merge project code can control its host through Docker; the deployer's Docker socket is root-equivalent.

## Bootstrap and cutover

Use the correct host directory (`spark-1` at `10.32.21.31`, `spark-2` at `10.32.21.32`). Confirm the pinned NVIDIA Container Toolkit supports CDI and the existing Docker daemon is healthy. No daemon runtime, inference launch recipe, firewall or host reboot is required for bootstrap.

Prepare runtime directories on that host:

```sh
sudo install -d -o 65534 -g 65534 -m 0755 /var/lib/spark-monitoring/{config,queue,textfile}
sudo install -d -o root -g 65534 -m 0750 /etc/spark-monitoring/credentials
sudo install -d -o root -g root -m 0755 /opt/spark-doco-cd /var/lib/spark-doco-cd/data
```

From a trusted workstation, extract only the selected host token. This example enrolls Spark 1; change both the key and destination together for Spark 2. Keep tracing disabled and the cluster age key on the workstation.

```sh
sops decrypt --extract '["stringData"]["SPARK_1_TOKEN"]' \
  kubernetes/apps/observability/external-metrics-ingest/app/auth.sops.yaml | \
ssh -o BatchMode=yes -o ConnectionAttempts=1 -o ConnectTimeout=5 kelchm@10.32.21.31 \
  'sudo sh -ec "umask 077; cat > /etc/spark-monitoring/credentials/token.next; test \$(wc -c < /etc/spark-monitoring/credentials/token.next) -eq 64; LC_ALL=C grep -Eq \"^[0-9a-f]{64}$\" /etc/spark-monitoring/credentials/token.next; chown root:65534 /etc/spark-monitoring/credentials/token.next; chmod 0640 /etc/spark-monitoring/credentials/token.next; mv /etc/spark-monitoring/credentials/token.next /etc/spark-monitoring/credentials/token"'
```

Copy the host's deployer Compose file from `platform/<host>/compose.yaml` to `/opt/spark-doco-cd/compose.yaml`, then run `sudo docker compose -f /opt/spark-doco-cd/compose.yaml up -d --wait`. Bootstrap Spark 1 first. Verify its declared projects and deployment labels; do not start both targets on one host.

Before proceeding to Spark 2, verify:

1. `/metrics` on 9100, 9835 and 8429 is reachable only through loopback; no exporter listens on Workloads, Storage or fabric addresses.
2. Every selected node collector succeeds, GPU collection succeeds with a fresh last-success timestamp, and all scrape targets are up. Only Spark 1 has the inference job.
3. Fresh samples with `host=spark-1` reach VMSingle through verified TLS. External series omit `cluster=k8s-prod`. Confirm token rejection and the effective positive/negative source policy from the ingestion runbook.
4. Stop the workstation pilot and verify one-second inference spacing and fresh dashboard values through the permanent path. The inference container's start time must be unchanged.
5. Measure scrape duration, exporter resource usage, queue bytes/second and inference behavior under the known three-session workload. Exercise a bounded receiver outage, collector restart and replay; verify historical samples and freshness after recovery. Confirm queue and configuration alerts route correctly once the host rules are activated.
6. Repeat host-only acceptance on Spark 2, and check carrier changes against its ongoing link investigation in #649. No inference or physical-link remediation is part of this project.

A 1 GiB queue cap bounds storage, not time. At the October 3 idle qualification, a 35-second outage accumulated about 180 KB (roughly 5.1 KB/s); extrapolating that short idle rate gives about 58 hours, but this is not a sustained-load retention guarantee. Measure the deployed rate and maintain free disk headroom. Overflow drops oldest data. Normal stops flush the in-memory tail; abrupt kills or power loss can lose the newest samples, and process-restart tests do not prove power-loss durability. Central alerts cannot page through a complete central outage; independent availability work remains in #629.

## Pause, rotation, recovery and removal

Stop `spark-doco-cd` to pause Git delivery. `unless-stopped` preserves that explicit stop across a host reboot; running collectors remain independent. Start it to resume. For a break-glass apply, keep the deployer stopped, validate and apply the selected host's Compose project from a trusted checkout, then merge the same change before resuming polling.

Rotate credentials with the ingestion runbook and atomic replacement of the token file. The credentials directory is mounted, so replacement is visible; vmagent re-reads the file every second. HTTP 401 responses buffer and retry. A stopped deployer or unavailable GitHub leaves collection running. An unavailable ingestion endpoint leaves a bounded queue; inspect `/metrics`, `/targets`, Docker logs and free space without deleting that queue.

Removing a doco-cd target does not stop a running project. Pause the deployer, explicitly run `docker compose -p <host>-monitoring down` with the host definition, revoke the credential and exact network allow, then remove the target. Retain or deliberately delete runtime state after deciding whether its queued history should replay. A rebuilt host needs its token, runtime paths and manual deployer bootstrap restored; Git exports contain no workload state.

## Qualification evidence

On October 3, 2026, the pinned ARM64 images ran in isolated, uniquely named projects on Spark 1 without changing the inference container. The narrowed node scrape returned 1,025 samples in 226 ms with every selected collector successful; GPU collection returned 76 samples in 46 ms. Carrier change/down/up counters, pressure and active endpoint RDMA state were observed. Spark 2's ARM64, Docker/Compose and NVIDIA driver versions matched; full exporter and sustained-load acceptance there remain rollout gates.

Actual Compose trials validated invalid-candidate retention, valid publication/revert without collector restart, persistent queue survival across a graceful collector restart, and receiver recovery/drain. The pinned doco-cd image against a disposable local Git fixture validated initial deployment, unrelated-commit inertness, invalid-config retention, revert/update and pause/resume. A separate pinned native vmagent/VictoriaMetrics fixture queried historical samples after graceful stop and hard kill; the latter recovered through approximately 0.6 seconds before termination. Temporary projects were removed. Host reboot survival is intended Docker behavior and remains a live maintenance-window check, not a reason to reboot an inference host for this evaluation.
