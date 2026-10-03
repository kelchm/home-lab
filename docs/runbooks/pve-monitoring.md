# PVE monitoring

PVE host metrics use the native [host-config channel](https://github.com/kelchm/home-lab/pull/698) and local persistent push to the [external ingestion endpoint](external-metrics.md). This runbook covers the separate read-only PVE API exporter in Kubernetes. It supplies guest, storage, cluster/quorum, HA/lock, version, backup-selection, subscription and replication facts that a node exporter cannot provide. [#650](https://github.com/kelchm/home-lab/issues/650) owns live acceptance.

**Prepared state:** the `pve-api-exporter` Flux Kustomization is suspended. Its dedicated API user/token and encrypted Secret have not been created. Merge adds reviewed code and the external scrape class; it does not activate API collection or enroll PVE hosts. Keep suspension until the credential, target policy and acceptance are ready.

## Deployment and trust

The pinned `prompve/prometheus-pve-exporter:3.10.1` runs as UID/GID 101 with a read-only filesystem, no capabilities or service-account token, and CPU/memory bounds. Its Service is internal only. The exporter uses verified HTTPS to each exact `pve-sbx-N.home.kelch.io:8006` name and a privilege-separated `metrics@pve!exporter` token. Both the user and token need `PVEAuditor` on `/`; no write role, root token or Homepage credential is used for permanent monitoring.

The workload's external Cilium allow covers only `10.32.20.21–23:8006`. The namespace baseline also trusts same-namespace traffic, DNS and the Kubernetes API. The multi-target exporter is an internal trusted service, not an endpoint for untrusted target selection. Requests remain bounded to the declared API targets operationally; keep its API credential out of URLs, logs and public dashboards. The cluster's ordinary node egress identity is used; this is not the independently isolated observability egress proposed in #355.

The Kubernetes exporter and vmagent have a central failure domain. Local host collection continues independently and buffers through central outages. API exporter process health does not prove an API target is reachable: inspect both `pve-api-cluster` and `pve-api-node` target health and sample age.

## Qualification

On October 3, 2026, the pinned Python exporter package ran on a trusted workstation against all three live PVE APIs, temporarily using the existing read-only Auditor credential there. Hostname verification succeeded. With the per-guest config collector disabled, each cluster scrape returned 368 samples in 0.33–0.96 seconds; each node-only scrape returned seven samples in 0.13–0.19 seconds. Metrics parsed without duplicate label sets. Cluster facts were repeated by all three sources; node-only facts were subscription and configured replication state. Credential scratch files and the exporter process were removed.

The pinned container image also ran credential-free TLS probes as UID 101 with the intended filesystem/capability protections from each Kubernetes node. All nine node-to-PVE combinations resolved the exact management IP, verified TLS and received the expected unauthenticated 401. The temporary three pods and exact-target Cilium policy were removed. This qualifies DNS, image trust roots and the current Kubernetes API route; it does not qualify the dedicated credential, final exporter process under load or its future firewall behavior.

The manifests pass Kubernetes schema checks and live server dry-run, including both VMStaticScrapes. Promtool fixture tests prove normalization of three source observations into one resource, exclusion of failed sources, and expiry of old observations. Actual authenticated container scraping, alert delivery and dashboards remain live acceptance gates.

## Credential bootstrap and activation

Perform the cluster account operation once on a PVE node after reviewing this procedure:

```sh
sudo pveum user add metrics@pve --comment 'Read-only metrics exporter'
sudo pveum acl modify / --users metrics@pve --roles PVEAuditor
sudo pveum user token add metrics@pve exporter --privsep 1
sudo pveum acl modify / --tokens 'metrics@pve!exporter' --roles PVEAuditor
```

Token creation prints its secret once. Capture that result directly into a private file on the trusted operator workstation with tracing disabled and `umask 077`; do not expose it in chat or a terminal recording. Extract the `value` into another private file, without putting it in an argument. Create `pve-api-credentials` in the `observability` namespace as an encrypted Git Secret with key `PVE_TOKEN_VALUE`, using `kubectl create secret generic --from-file=PVE_TOKEN_VALUE=/private/path/token --dry-run=client -o yaml`. Set the namespace, write it to `kubernetes/apps/observability/pve-api-exporter/app/credentials.sops.yaml`, encrypt it immediately with SOPS, and add it to the app's Kustomization. Delete the plaintext temporary files after verifying encryption. Never distribute the age key to PVE.

Inspect both ACLs and the token's privilege separation. Revalidate verified HTTPS with this dedicated token on the workstation and check that all required families are returned. Review a separate activation change containing the encrypted Secret and `suspend: false`; confirm the external scrape class has reconciled first. Do not make the namespace baseline depend on a deliberately suspended Kustomization.

After activation, require the exporter Ready and all six declared cluster/node API targets up. Verify external targets carry `cluster=pve-sbx`, not `k8s-prod`, and distinct `api_source` labels. Compare a guest and node against the PVE console; normalized totals must agree with one cluster observation. Stop one API scrape in a bounded acceptance test, verify remaining fresh sources retain the facts, then verify target/all-source alert behavior and recover. Test notification delivery through the normal alert route. Record the actual acceptance and account creation in #650 and the PVE service-account documentation.

## Source normalization and interpretation

Cluster scrapes use `cluster=1,node=0`; node scrapes use `cluster=0,node=1`. Disabling the config collector avoids an API call for every guest. Raw cluster facts retain their API source for diagnosis. The `pve:*:dedup` records use only successful cluster sources whose sample timestamp is less than 90 seconds old and remove source/instance/job identity before aggregation. They use the conservative minimum for `pve_up` and maximum for resource gauges/counters. API observations may briefly disagree during migration or restart; these are conservative coarse cluster facts, not precise per-guest accounting. Do not sum raw replicated resource series. Info labels can differ transiently; count guest/resource IDs once when joining them.

Quorum loss, offline nodes, missing sources and uncovered persistent guests have bounded alerts. Template and disposable guest state is not a generic offline-guest page. `pve_not_backed_up_info` reports configured job selection only: it does not report completed backups, recovery points or restore success. PVE's current backup email delivery is broken, as recorded in [the operator notes](../../proxmox/README.md#shared-storage); independent backup execution monitoring remains unfinished work, not implied by these alerts.

## Rotation and retirement

Rotate the dedicated token deliberately, update its encrypted Secret and verify the exporter rollout plus all target health. Environment credentials are read at process startup. Deleting a Secret key alone blocks new pods and leaves old pods holding their old value; revoke the PVE token itself for immediate credential invalidation.

To retire API monitoring, suspend delivery and explicitly remove the Deployment/Service/scrapes/rules, revoke the dedicated token and user only after checking ownership, and remove the encrypted Secret and exact external policy. Host exporters and their queues have separate ownership and continue independently. Removing Git declarations while Flux is suspended does not remove running objects; either unsuspend a reviewed removal or deliberately remove the owned objects and record it.
