# Alert Delivery and Testing

Operational procedure for VMAlertmanager in `observability`, including ownership, routing, silences, delivery tests, and control-plane coverage checks.

## Ownership and routing

The homelab operator owns every alert. `critical` means act as soon as the notification arrives; `warning` means investigate before the condition can consume redundancy or capacity. Alerts without one of those severities stay in Alertmanager for inspection but do not notify externally.

Alertmanager routes as follows:

| Match | Receiver | Repeat | Resolution |
|---|---|---:|---|
| `severity="critical"` and `evaluator="vmalert"` | `k8s-prod Alerts` in Pushover, high priority | 12 hours | Quiet priority |
| `severity="warning"` and `evaluator="vmalert"` | `k8s-prod Alerts` in Pushover, normal priority | 12 hours | Quiet priority |
| `alertname="Watchdog"` and `evaluator="vmalert"` | `healthchecks-watchdog` webhook to healthchecks.io | 1 minute | None |
| Everything else | `null` | N/A | N/A |

vmalert is the delivery authority and the cluster's only rule evaluator. Its notifier configuration (`vmalert.additionalNotifierConfigs`) adds `evaluator=vmalert` to every alert it sends to VMAlertmanager. The label exists only on notifications: vmalert does not write it to `ALERTS` or recording-rule output, so joins between raw and recorded series need no `ignoring(evaluator)`. `cluster` comes from each rule group, as described in [Cluster labels](#cluster-labels). The SOPS-encrypted `vmalertmanager-config` Secret owns VMAlertmanager routing, the Pushover application token/user key, and the healthchecks.io ping URL. Do not put any of these credentials in Helm values, shell history, issue comments, or screenshots.

Use a source-specific Pushover application for each independent alert producer. Kubernetes uses `k8s-prod Alerts`; a future Proxmox setup should use a separate application such as `pve-prod Alerts` rather than sharing this token. Both applications can deliver to the same Pushover user and devices while retaining distinct names, icons, quotas, audit history, and revocation boundaries.

Firing critical alerts use Pushover priority `1`, which bypasses quiet hours but does not create emergency acknowledgements. Firing warnings use priority `0`; resolved notifications use quiet priority `-1`. Emergency priority `2` remains disabled until retry and acknowledgement behavior has been deliberately designed.

## First response

1. Open `https://alertmanager.home.kelch.io` and inspect the complete label and annotation set. Pushover is a prompt to investigate, not the full source of truth.
2. Confirm the alert has `evaluator=vmalert`. Any alert without that label did not come from the production evaluator.
3. Follow the alert description and linked subsystem runbook. For Flux failures, inspect the named Kustomization or HelmRelease before forcing a reconcile. For Longhorn backup alerts, use [longhorn-backup-restore](longhorn-backup-restore.md#routine-monitoring). For alerts about the monitoring stack itself, the [Observability Pipeline](https://grafana.home.kelch.io/d/observability-pipeline/observability-pipeline) dashboard shows which stage is failing: scrape and store, evaluate and notify, or log collection.
4. Silence only when the cause and maintenance window are understood. Fix the signal or its rule instead of leaving a recurring silence.

Useful inventory commands:

```sh
kubectl -n observability get prometheusrules
kubectl -n observability get vmalertmanager victoria-metrics-k8s-stack
kubectl -n observability logs vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager --since=30m
kubectl -n flux-system get kustomizations,helmreleases
```

## Silences

Prefer the Alertmanager UI. Match the smallest stable label set—normally `alertname` plus `namespace`, and a workload/PVC label when present. Every silence needs a bounded duration and a comment containing the reason and maintenance reference.

The CLI equivalent runs `amtool` inside the Alertmanager pod:

```sh
kubectl -n observability exec vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence add \
  alertname=KubeFailedPodChurn namespace=example \
  --duration=1h --comment='planned controller repair'
```

List and expire a silence explicitly:

```sh
kubectl -n observability exec vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence query
kubectl -n observability exec vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence expire <silence-id>
```

Never silence all `critical` alerts or all alerts from a namespace. Do not silence `Watchdog`: it is the [dead-man heartbeat](#independent-dead-man-heartbeat), so any silence longer than about ten minutes pages through healthchecks.io. For planned whole-cluster downtime, pause the healthchecks.io check as described there.

## End-to-end delivery test

Run after changing Alertmanager, routing, credentials, or the monitoring stack. This temporary rule is intentionally not committed:

```sh
kubectl -n observability apply -f - <<'EOF'
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: observability-pipeline-test
spec:
  groups:
    - name: observability-pipeline-test
      labels:
        cluster: k8s-prod
      rules:
        - alert: ObservabilityPipelineTest
          expr: vector(1)
          for: 1m
          labels:
            severity: warning
            test: synthetic
          annotations:
            summary: Synthetic observability delivery test
            description: This alert is expected during an operator-initiated end-to-end test.
EOF
```

Within roughly two minutes, verify all of the following:

- `ObservabilityPipelineTest` is firing in VMAlertmanager with `cluster=k8s-prod` and `evaluator=vmalert`.
- Exactly one normal-priority Pushover notification arrives from `k8s-prod Alerts` through the VM path.
- `alertmanager_notifications_failed_total{integration="pushover"}` does not increase.

Delete the rule and confirm exactly one quiet-priority resolved notification arrives through the VM path and that the Pushover failure counter does not increase:

```sh
kubectl -n observability delete prometheusrule observability-pipeline-test
```

## Longhorn stale-backup delivery test

This tests the real Longhorn timestamp metric without taking the NFS target offline. It temporarily treats every nonzero backup age as stale and uses the production alert name with a distinguishing `test=synthetic` label:

```sh
kubectl -n observability apply -f - <<'EOF'
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: longhorn-backup-delivery-test
spec:
  groups:
    - name: longhorn-backup-delivery-test
      labels:
        cluster: k8s-prod
      rules:
        - alert: LonghornBackupsStalled
          expr: time() - max(longhorn_volume_last_backup_at > 0) > 0
          for: 1m
          labels:
            severity: critical
            test: synthetic
          annotations:
            summary: Synthetic Longhorn stale-backup delivery test
            description: This alert is expected during an operator-initiated end-to-end test.
EOF
```

Confirm one high-priority Pushover notification, then clean up and confirm a quiet-priority resolution:

```sh
kubectl -n observability delete prometheusrule longhorn-backup-delivery-test
```

## Independent dead-man heartbeat

vmalert's always-firing `Watchdog` routes to the `healthchecks-watchdog` webhook, which POSTs to the healthchecks.io check `k8s-prod Watchdog` every two minutes. The check expects a ping every 5 minutes with a 5-minute grace period, so a heartbeat that stops for roughly ten minutes pages through healthchecks.io's own Pushover integration and email. Neither notification depends on this cluster. The next ping after recovery sends an "up" notification. The route sets `send_resolved: false`, because a resolved webhook would reach the same ping URL and count as a heartbeat.

The heartbeat stops if the cluster, vmalert evaluation (Watchdog's `vector(1)` is evaluated against VMSingle), VMAlertmanager, DNS, the `vmalertmanager` egress policy, or the WAN fails. It does not exercise the `k8s-prod Alerts` Pushover credentials; the delivery tests above do.

The check's settings are declared in [`healthchecks/checks.json`](../../healthchecks/checks.json) and applied as described in [healthchecks.io checks](#healthchecksio-checks).

To test, silence the heartbeat for longer than the detection window:

```sh
kubectl -n observability exec vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence add \
  alertname=Watchdog --duration=20m --comment='dead-man heartbeat test'
```

Confirm the healthchecks.io "down" Pushover and email arrive within about ten minutes, then expire the silence and confirm the "up" notification follows within about two minutes.

Verified 2026-09-27 in #218: a 20-minute silence produced the "down" Pushover and email 10 minutes after the last ping, and the "up" notification arrived 18 seconds after the silence expired, with zero webhook delivery failures.

For planned whole-cluster downtime, pause the check after the heartbeat stops, not before: any ping resumes a paused check. Shut the cluster down, confirm healthchecks.io shows no ping since, and pause within ten minutes of the last ping. The first ping after the cluster returns resumes monitoring; confirm the check shows up.

The ping URL is a credential: anyone who holds it can mask an outage by pinging. To rotate it, declare a replacement check with a temporary slug and the same name, settings and integrations, apply it, replace the URL in `vmalertmanager-config.sops.yaml`, and confirm the replacement is up. Then explicitly delete the retired check in the web UI, rename the replacement's slug to `k8s-prod-watchdog`, and remove its temporary entry from the file while retaining the original declaration. Run preview and apply again to verify that the replacement matches the declaration and has the Watchdog safeguards. Renaming the slug preserves the UUID-based ping URL handed to the cluster.

## healthchecks.io checks

[`healthchecks/checks.json`](../../healthchecks/checks.json) declares every check in the `home-lab` project: name, slug, tags, description, timing (`timeout`, or `schedule` with `tz`), `grace` and `channels`. `timeout` and `grace` are in seconds; `schedule` is a cron expression and `tz` is its timezone. [`scripts/healthchecks.py`](../../scripts/healthchecks.py) applies the file from a workstation by upserting each check on its slug with the [Management API v3](https://healthchecks.io/docs/api/#create-check), which keeps an existing check's ping URL. It never deletes: a check that exists in the project but not in the file is listed and left alone, and removing one is a deliberate step in the web UI.

Integrations are configured per project in the web UI and are not in the file. Pushover sends "down" at high priority and "up" at low priority, and email goes to the account address. `channels` is always `"*"`, which assigns every project integration to the check.

The script needs the project's read-write API key, 1Password item `healthchecksio-home-lab-rw`. Export `HC_API_KEY_REF` with that item's secret reference and the script reads the key through `op read`; `HC_API_KEY_FILE` can name a local file holding the key instead. The key can delete checks and reveals every ping URL, so it stays on the workstation and never goes to the cluster or Athena.

```sh
export HC_API_KEY_REF="op://Private/healthchecksio-home-lab-rw/credential"
task healthchecks:inspect   # live settings of every check, in the file's shape
task healthchecks:preview   # live settings, differences, and the requests an apply would send
task healthchecks:apply     # the same plan, then the requests after typing "apply"
```

Preview and inspect only read. Apply refuses before sending anything when a declared slug matches more than one live check, or when `k8s-prod-watchdog` is missing, is not up, or would get different timing: the script updates that check by its existing UUID so a concurrent removal fails instead of creating a replacement, and it never changes its schedule. After sending, apply reads the project back and fails unless every declared check matches the file, has every project integration, and kept the ping URL and status it had before; `k8s-prod-watchdog` must also still be up. Changing a check's timing makes healthchecks.io recompute its status from the last ping, so apply a schedule change while the check is up and expect the status comparison to flag any flip.

Initial adoption verified 2026-10-05 for #770: both existing checks matched the file after apply and retained their ping URLs and `up` status; Watchdog had no status flips during the acceptance test. A throwaway check was created from the file, remained untouched after removal from the file and another apply, and was then explicitly deleted by slug.

To add a check for a new job:

1. Add it to `healthchecks/checks.json` with a unique slug and run `task healthchecks:preview`. The new check shows as `CREATE`.
2. Merge the change, then run `task healthchecks:apply`.
3. Copy the new check's ping URL from the healthchecks.io web UI into the job's own SOPS-encrypted secret. A ping URL is a credential: the script never prints one, and it stays out of the file, the terminal and issue comments.
4. Run the job, or wait for its first run, and confirm the check goes up.

## Cluster labels

`cluster` names a real clustered system. It is set where each series or alert originates rather than globally, so scrape targets and rules outside `k8s-prod` do not inherit it.

- **Scrapes:** vmagent's default `kubernetes` scrape class adds `cluster=k8s-prod` to every scrape object that names no class. A target outside the cluster must name a different scrape class, defined on the VMAgent, that sets its own identity labels. Never add `externalLabels.cluster` to vmagent. Together with the class, it renames the label to `exported_cluster` on every scrape without `honorLabels`. VMProbe objects inherit only authentication from a class, so each probe sets its own labels.
- **Rules:** every Kubernetes rule group carries `labels: {cluster: k8s-prod}`. Bundled rules get it through the chart's `defaultRules.group.spec`, repo rules on each group, and kaniop's group through a post-renderer. Use a group label, not a per-rule label: vmalert derives a rule's ID from the rule's own labels, so changing per-rule labels resets that rule's alert state. Bundled rule groups also carry `params: {extra_filters[]: ['{cluster="k8s-prod"}']}` and repo rules that select generic host metrics match `cluster="k8s-prod"`, so series from the [PVE and Spark hosts](host-monitoring.md) never reach them. Rules about systems outside the cluster omit the label and keep their source series' labels.

`count by (job) ({__name__=~".+", cluster=""})` returns no rows while every Kubernetes series has the label; any row names a scrape job whose series lack it. CNPG's database exporters expose their own `cluster` label, which vmagent keeps as `exported_cluster` (4 series on 2026-09-27); any other `exported_cluster` means a source collided with the scrape class.

## Coverage checks

VM-native ownership must produce one healthy kube-state-metrics target; three healthy targets for node-exporter and each Talos control-plane job; three healthy API-server endpoints; three healthy kubelet endpoints for each enabled path; and the live CoreDNS replica count:

```promql
count by (job) (up{job="kube-state-metrics"} == 1) == 1
and on (job)
count by (job) (up{job="kube-state-metrics"}) == 1

count by (job) (up{job=~"node-exporter|kube-(controller-manager|scheduler|etcd)"} == 1) == 3
and on (job)
count by (job) (up{job=~"node-exporter|kube-(controller-manager|scheduler|etcd)"}) == 3

count(up{job="apiserver"} == 1) == 3
and
count(up{job="apiserver"}) == 3

count by (metrics_path) (up{job="kubelet"} == 1) == 3
and on (metrics_path)
count by (metrics_path) (up{job="kubelet"}) == 3

count(up{job="core-dns"} == 1) == count(kube_pod_status_ready{namespace="kube-system",pod=~"coredns-.*",condition="true"} == 1)
and
count(up{job="core-dns"}) == count(kube_pod_status_ready{namespace="kube-system",pod=~"coredns-.*",condition="true"} == 1)
```

Expected results: `1` for kube-state-metrics, `3` for node-exporter and each Talos control-plane job, `3` for every enabled kubelet metrics path, and the current ready CoreDNS replica count. The equalities reject missing, unhealthy, or duplicate targets. Confirm VM-native resources are the only pools for kube-state-metrics, node-exporter, API-server, kubelet, CoreDNS, controller-manager, scheduler, and etcd in the vmagent targets UI; there must be no converted KPS copies. Controller-manager and scheduler scrape over HTTPS with the vmagent service-account bearer token. Those components generate self-signed certificates with only `localhost` and `127.0.0.1` subject alternatives, while VM scrapes their node IPs, so the scrapes skip certificate verification while retaining transport encryption and authorization. Etcd's separate HTTP listener exposes metrics only and receives no bearer token.

Check the rest of the signal path with:

```promql
ALERTS{alertstate="firing",severity=~"warning|critical"}
alertmanager_config_last_reload_successful{job="vmalertmanager-victoria-metrics-k8s-stack"}
alertmanager_notifications_failed_total{job="vmalertmanager-victoria-metrics-k8s-stack",integration=~"pushover|webhook"}
longhorn_backup_target_available{backup_target="default"}
```

The operator-generated VMServiceScrape gives VMAlertmanager the `job="vmalertmanager-victoria-metrics-k8s-stack"` label. The steady state has the vmalert `Watchdog` in VMAlertmanager, `alertmanager_config_last_reload_successful == 1`, no firing warning/critical alerts, no Pushover or webhook delivery failures, the healthchecks.io check up, and `longhorn_backup_target_available == 1`.

If the vmalert `Watchdog` disappears from VMAlertmanager, healthchecks.io pages within about ten minutes. Treat that as an observability incident and check vmalert rule health, VMAlertmanager ingestion, and `alertmanager_notifications_failed_total{integration="webhook"}`.

## Grafana survivor check

Open Grafana's data-source settings and confirm VictoriaMetrics (`victoriametrics`) is the default metrics datasource and Alertmanager (`alertmanager-vm`) resolves through VMAlertmanager. No `Prometheus` or `Loki` datasource should be present. Render representative cluster, node-exporter, and node-hardware dashboards over a recent time range, then use Explore against VictoriaMetrics to confirm the target-count and Longhorn queries above return current data without materially duplicated series.

## Credential rotation

Reset the API token on the Pushover application `k8s-prod Alerts` and replace it in `kubernetes/apps/observability/victoria-metrics-k8s-stack/app/vmalertmanager-config.sops.yaml` in the same maintenance window. The Pushover user key normally remains stable, but update it too if the destination account changes. Reconcile `victoria-metrics-k8s-stack`, verify `alertmanager_config_last_reload_successful == 1`, run the end-to-end test above, and consider the old token revoked only after the new notification arrives.
