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

Healthchecks.io check settings:

| Setting | Value |
|---|---|
| Schedule | Simple: period 5 minutes, grace 5 minutes |
| Pushover integration | Down: high priority; up: low priority |
| Email integration | Account email, enabled |

To test, silence the heartbeat for longer than the detection window:

```sh
kubectl -n observability exec vmalertmanager-victoria-metrics-k8s-stack-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence add \
  alertname=Watchdog --duration=20m --comment='dead-man heartbeat test'
```

Confirm the healthchecks.io "down" Pushover and email arrive within about ten minutes, then expire the silence and confirm the "up" notification follows within about two minutes.

Verified 2026-09-27 in #218: a 20-minute silence produced the "down" Pushover and email 10 minutes after the last ping, and the "up" notification arrived 18 seconds after the silence expired, with zero webhook delivery failures.

For planned whole-cluster downtime, pause the check after the heartbeat stops, not before: any ping resumes a paused check. Shut the cluster down, confirm healthchecks.io shows no ping since, and pause within ten minutes of the last ping. The first ping after the cluster returns resumes monitoring; confirm the check shows up.

The ping URL is a credential: anyone who holds it can mask an outage by pinging. To rotate it, create a replacement check with the same schedule and integrations, replace the URL in `vmalertmanager-config.sops.yaml`, confirm pings arrive on the new check, and then delete the old check.

## Cluster labels

`cluster` names a real clustered system. It is set where each series or alert originates rather than globally, so scrape targets and rules outside `k8s-prod` do not inherit it.

- **Scrapes:** vmagent's default `kubernetes` scrape class adds `cluster=k8s-prod` to every scrape object that names no class. A target outside the cluster must name a different scrape class, defined on the VMAgent, that sets its own identity labels. Never add `externalLabels.cluster` to vmagent. Together with the class, it renames the label to `exported_cluster` on every scrape without `honorLabels`. VMProbe objects inherit only authentication from a class, so each probe sets its own labels.
- **Rules:** every Kubernetes rule group carries `labels: {cluster: k8s-prod}`. Bundled rules get it through the chart's `defaultRules.group.spec`, repo rules on each group, and kaniop's group through a post-renderer. Use a group label, not a per-rule label: vmalert derives a rule's ID from the rule's own labels, so changing per-rule labels resets that rule's alert state. Bundled rule groups also carry `params: {extra_filters[]: ['{cluster="k8s-prod"}']}` and repo rules that select generic host metrics match `cluster="k8s-prod"`, so series from [hosts outside the cluster](external-hosts.md) never reach them. Rules about systems outside the cluster omit the label and keep their source series' labels.

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
