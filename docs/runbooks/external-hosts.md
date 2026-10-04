# External host health

The **External Hosts** dashboard in Grafana's Hardware folder covers PVE host and DGX Spark metrics delivered by local vmagent. [Ingestion](external-metrics.md), [Spark deployment](../../sparks/host-monitoring/README.md), [PVE host configuration](https://github.com/kelchm/home-lab/pull/698), and [PVE API monitoring](https://github.com/kelchm/home-lab/pull/700) have separate ownership. These views do not deploy an exporter, create a credential or enroll a host.

## Enrollment and acceptance

All five `external_host:expected` records in `kubernetes/apps/observability/victoria-metrics-k8s-stack/app/external-host-alerts.yaml` initially use `vector(0)`. Host alerts are disabled until that host's live canary passes. Change only the accepted host to `vector(1)` in a reviewed activation change. The dashboard lists prepared hosts even before data arrives; **No data means unknown**, not zero or healthy. To retire a host, set its record back to zero before removing its collector. Do not silence the entire external fleet to retire one host.

Before enrollment, verify the expected host/platform/job labels, fresh node/GPU/collector targets, applied configuration and normal queue drain. Exercise the planned receiver outage and recovery, verify a bounded collector stop produces the expected missing-metrics alert, and test normal notification delivery. GPU acceptance includes a load sample and a failed/timed-out command, not only an idle temperature. Record evidence in [#697](https://github.com/kelchm/home-lab/issues/697) for Sparks or [#650](https://github.com/kelchm/home-lab/issues/650) for PVE. Existing pilot inference monitoring has its own cutover gate in [#653](https://github.com/kelchm/home-lab/issues/653).

## Current health and replay

Node and vmagent health require `up=1` with the original scrape timestamp less than 90 seconds old. A missing/down/old node scrape becomes `ExternalHostMetricsMissing` after five minutes. Downstream alerts require a fresh node scrape, reducing duplicate pages when the whole host path is lost. GPU health also requires a successful native command within 20 seconds; an HTTP 200 serving cached GPU values is insufficient. Thermal alerts use the actual hardware/software thermal slowdown flags for five minutes. Ordinary power-cap flags are not a thermal fault.

Graphs intentionally show historical samples replayed after an outage. A graph ending in old data is not current health; use the current scrape-health panel and command age. Host timestamps require working time synchronization. Remote push cannot diagnose whether a complete missing path is host power, local collection, credentials, network or receiver failure; inspect those in order. Central stack loss also prevents central alert delivery. Independent edge availability remains [#629](https://github.com/kelchm/home-lab/issues/629), and the existing alerting Watchdog covers its documented external route.

## Queue and delivery failures

A nonzero queue continuously for 15 minutes warns of delayed delivery. Persistent bytes discarded in the last 15 minutes warn of data loss. Each queue has a 1 GiB disk cap, not a guaranteed retention time; the newest in-memory samples can be lost on abrupt power loss. A host that cannot reach the receiver cannot send its own queue warning at that moment, so missing fresh host metrics is the primary signal. After recovery, examine backlog, discarded bytes and replayed sample times together.

The dashboard shows local runtime configuration acceptance and Git delivery state. Spark publisher failure retains the prior validated config; vmagent reload failure similarly requires its logs. Spark deployer health requires a fresh target and a poll within 15 minutes. A deliberate deployer pause needs an explicit temporary silence and an owner/revisit time. PVE failure/dirty/drift/unknown-check status warns after 15 minutes; dirty also covers reconvergence pending after explicit engine installation; status older than 15 minutes or absent warns while node metrics remain fresh. Pausing the PVE engine does not itself page: its timer still reports status, and the applied exporter keeps collecting. Git outages do not discard applied configuration.

## Hardware interpretation

GB10 uses unified memory: available host memory and memory pressure are capacity signals; framebuffer N/A and GPU memory-utilization are not memory capacity. RDMA and carrier/error panels are endpoint counters, not switch fabric health. Spark 2 carrier remediation remains [#649](https://github.com/kelchm/home-lab/issues/649). Filesystem panels show real host mounts and queue storage context; unsupported collectors leave panels unknown. PVE guest, quorum, subscription, replication and configured backup selection belong to the separately activated API exporter. Backup selection does not prove backup completion or restore success.
