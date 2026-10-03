# External metrics ingestion

The repository defines host-local collectors pushing to `https://metrics-ingest.home.kelch.io/api/v1/write`, backed by the existing VMSingle. VMAuth runs on the dedicated `services-prod` VIP `10.32.140.2:443`. Each Spark and PVE host has a separate bearer token; exporters remain local to their host. Inference and hypervisor maintenance have separate owners.

**Rollout:** [#694](https://github.com/kelchm/home-lab/issues/694) owns live ingestion acceptance. Complete its DNS, TLS, network and real-write checks before enrolling permanent collectors; merging this configuration does not establish live acceptance. Keep the workstation TensorFold pilot until permanent cutover is verified.

## Access and ownership

The LoadBalancer preserves source addresses with `externalTrafficPolicy: Local`. Cilium permits the five management IPs (`10.32.21.31–32`, `10.32.20.21–23`) to the write listener only. The observability namespace baseline additionally trusts namespace and node traffic. Credentials authorize writes into a shared trusted-host dataset: they do not enforce series-label ownership or tenant isolation. A compromised collector can write misleading series, so revoke its token and remove its network access when recovering that host.

VMAuth accepts only `/api/v1/write` on the public listener and strips the bearer header before forwarding. Query, import, deletion, debug, reload and metrics paths have no route. Its internal listener on port 8426 serves TLS health and metrics through a ClusterIP service; it has no LoadBalancer port. Both listeners use the certificate for `metrics-ingest.home.kelch.io`. VMAuth reads updated TLS certificates on new handshakes; credential changes trigger a Deployment rollout; two replicas and a disruption budget preserve one available replica during routine updates. This does not make VMSingle or the central cluster independent of their failure domain.

Flux owns the VMAuth deployment, certificate, encrypted tokens, services and monitoring objects. cert-manager owns TLS issuance and renewal. Host collectors own their persistent queue and token file outside the Git checkout. Do not distribute the cluster's SOPS age key to a monitored host.

## Initial rollout

After reviewing and merging the ingestion PR, verify the Flux Kustomization, Certificate and both VMAuth replicas are Ready. Confirm `10.32.140.2` was allocated without conflict. The Service hostname annotation lets k8s-gateway resolve the name; verify it through each host's usual resolver. Use a precise UniFi local DNS record if the normal resolver does not forward this name to k8s-gateway.

UniFi is manually managed. Before relying on ingestion, allow the five host management addresses to `10.32.140.2`, TCP 443, then deny that destination/port from all other routed sources. Order both rules above broad services-pool allows; adding an allow alone does not restrict the currently permitted Workloads-to-services path. Keep return traffic stateful. The BGP-routed pool is classified as External in the current controller: use the verified zone classification in [the UniFi operating notes](../../network/unifi/README.md), and test it after the change. Do not allow the entire admin or services pool for this task. Document the effective rule ordering and positive/negative results in #694.

Extract only the selected host's token to its local root-owned token file during bootstrap. From a workstation with SOPS access, pipe decryption directly into the extractor and bootstrap; do not print or put a token in an argument, shell history, Git checkout or Compose environment. For example, the Spark 1 token is `.stringData.SPARK_1_TOKEN` in `kubernetes/apps/observability/external-metrics-ingest/app/auth.sops.yaml`; PVE uses `.stringData.PVE_SBX_1_TOKEN`, and similarly for the other hosts. The host-specific collector runbook supplies its exact installation path and command.

Verify from the canary host that normal certificate verification succeeds, an authenticated real remote write reaches VMSingle, an invalid token is rejected, and authenticated query/admin/internal requests fail. Verify TCP 443 is denied from an unlisted source and port 8426 is not externally reachable. Repeat positive ingestion from each enrolled host. Do not weaken TLS verification to make a test pass. Confirm VMAuth scrapes and `ExternalMetricsIngestUnavailable` routing, then verify host freshness and queue alerts with the collector rollout. A green VMAuth scrape alone does not prove storage ingestion.

## Credentials, outages and removal

Generate each token as 32 random bytes encoded in hex (`openssl rand -hex 32`); this avoids YAML escaping and keeps host credentials distinct. Every auth placeholder has a required `secretKeyRef`, so removing a key prevents new replicas from starting rather than making the placeholder a known credential. [Kubernetes requires referenced Secret keys](https://kubernetes.io/docs/concepts/configuration/secret/#optional-secrets); existing replicas retain their environment until replaced.

Rotate one host token with `sops edit` on `auth.sops.yaml`, review and merge the encrypted change, then atomically replace that host's token file. Keep collection running while authentication failures buffer; vmagent re-reads `bearerTokenFile` every second. Verify queue replay after the VMAuth rollout. The old token stops working after the VMAuth rollout; other host tokens continue to work. Rotation can briefly interrupt that host's writes, which its disk queue handles. For immediate revocation, stop the affected collector and remove its exact network allow; then rotate the Git-managed token. Git remains the durable configuration authority.

An ingestion or Git outage must leave host collection running. The host's persistent vmagent queue retries remote writes. A queue is bounded and drops old data on overflow: capacity and retention must be measured for the deployed scrape set rather than inferred from a disk-size setting. Central alerts cannot page during a full central outage; the external Watchdog and independent reachability work in #629 cover that gap. When removing a collector, explicitly stop and remove its service/project before deleting the Git target, remove its auth user, required Deployment environment entry and Secret key together in the same change, and retain or deliberately remove its queued state. Deleting only the Secret key blocks the VMAuth rollout and does not revoke the credential still held by existing replicas.

## Reproduce the authorization qualification

Use the pinned VMAuth v1.152.0 binary from the [official release](https://github.com/VictoriaMetrics/VictoriaMetrics/releases/tag/v1.152.0), verified against its published checksums:

```sh
VMAUTH_BIN=/path/to/vmauth-prod python3 scripts/monitoring/test-ingest.py
```

The test uses the repository's auth configuration and Deployment arguments, fixture credentials, an ephemeral TLS certificate and a local fake receiver. It checks that every token placeholder matches a required Deployment environment entry and an encrypted Secret key, and that empty credentials refuse startup. `yq` and OpenSSL are required. It verifies five valid host credentials, denied unauthenticated/invalid writes, denied read/admin/internal paths, internal health/metrics, bearer stripping and independent token rotation. It does not prove the live BGP, DNS, UniFi or Cilium paths. [VMAuth's documentation](https://docs.victoriametrics.com/victoriametrics/vmauth/) describes route matching and the separate internal listener.
