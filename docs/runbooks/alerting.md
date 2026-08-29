# Alert Delivery and Testing

Operational procedure for the shared Alertmanager in `observability`, including ownership, routing, silences, delivery tests, and control-plane coverage checks.

## Ownership and routing

The homelab operator owns every alert. `critical` means act as soon as the notification arrives; `warning` means investigate before the condition can consume redundancy or capacity. Alerts without one of those severities stay in Alertmanager for inspection but do not notify externally.

Alertmanager routes as follows:

| Match | Receiver | Repeat | Resolution |
|---|---|---:|---|
| `severity="critical"` and `prometheus="observability/kube-prometheus-stack-prometheus"` | `k8s-prod Alerts` in Pushover, high priority | 12 hours | Quiet priority |
| `severity="warning"` and `prometheus="observability/kube-prometheus-stack-prometheus"` | `k8s-prod Alerts` in Pushover, normal priority | 12 hours | Quiet priority |
| `alertname="Watchdog"` | `null` | N/A | N/A |
| Everything else | `null` | N/A | N/A |

Prometheus and vmalert both evaluate the shared `PrometheusRule` set and send to the same Alertmanager, but they add different source labels and therefore do not have identical fingerprints. Prometheus is the delivery authority; the `prometheus="observability/kube-prometheus-stack-prometheus"` matcher prevents the vmalert comparison copy from producing a second Pushover notification. Both attach `cluster=k8s-prod`. The SOPS-encrypted `alertmanager-config` Secret owns routing and the Pushover application token/user key; do not put either value in Helm values, shell history, issue comments, or screenshots.

Use a source-specific Pushover application for each independent alert producer. Kubernetes uses `k8s-prod Alerts`; a future Proxmox setup should use a separate application such as `pve-prod Alerts` rather than sharing this token. Both applications can deliver to the same Pushover user and devices while retaining distinct names, icons, quotas, audit history, and revocation boundaries.

Firing critical alerts use Pushover priority `1`, which bypasses quiet hours but does not create emergency acknowledgements. Firing warnings use priority `0`; resolved notifications use quiet priority `-1`. Emergency priority `2` remains disabled until retry and acknowledgement behavior has been deliberately designed.

## First response

1. Open `https://alertmanager.home.kelch.io` and inspect the complete label and annotation set. Pushover is a prompt to investigate, not the full source of truth.
2. Confirm whether both evaluators agree. Their copies can appear separately in Alertmanager because source labels differ, but only the Prometheus copy routes externally.
3. Follow the alert description and linked subsystem runbook. For Flux failures, inspect the named Kustomization or HelmRelease before forcing a reconcile. For Longhorn backup alerts, use [longhorn-backup-restore](longhorn-backup-restore.md#routine-monitoring).
4. Silence only when the cause and maintenance window are understood. Fix the signal or its rule instead of leaving a recurring silence.

Useful inventory commands:

```sh
kubectl -n observability get prometheusrules
kubectl -n observability get alertmanager kube-prometheus-stack-alertmanager
kubectl -n observability logs alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager --since=30m
kubectl -n flux-system get kustomizations,helmreleases
```

## Silences

Prefer the Alertmanager UI. Match the smallest stable label set—normally `alertname` plus `namespace`, and a workload/PVC label when present. Every silence needs a bounded duration and a comment containing the reason and maintenance reference.

The CLI equivalent runs `amtool` inside the Alertmanager pod:

```sh
kubectl -n observability exec alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence add \
  alertname=KubeFailedPodChurn namespace=example \
  --duration=1h --comment='planned controller repair'
```

List and expire a silence explicitly:

```sh
kubectl -n observability exec alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence query
kubectl -n observability exec alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 silence expire <silence-id>
```

Never silence all `critical` alerts or all alerts from a namespace. Do not silence `Watchdog` during an ordinary maintenance window; its presence in Alertmanager proves that rule evaluation and Alertmanager ingestion are working. External dead-man monitoring is still required to detect failures of Alertmanager itself or its outbound delivery path.

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

- `ObservabilityPipelineTest` is firing in Alertmanager with `cluster=k8s-prod`.
- Exactly one normal-priority Pushover notification arrives from `k8s-prod Alerts` because only the Prometheus evaluator is routed externally.
- `alertmanager_notifications_failed_total{integration="pushover"}` does not increase.

Delete the rule and confirm a resolved notification arrives:

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

## Coverage checks

All three control-plane instances for each job must report `up == 1`:

```promql
count by (job) (up{job=~"kube-(controller-manager|scheduler|etcd)"}) == 3
and on (job)
min by (job) (up{job=~"kube-(controller-manager|scheduler|etcd)"}) == 1
```

Expected result: `3` for each job. The count rejects missing or duplicate targets, while the minimum requires every discovered target to be healthy. Controller-manager and scheduler scrape over HTTPS with the Prometheus service-account bearer token. Talos issues localhost-only serving certificates for those components, so the monitors skip certificate verification while retaining transport encryption and authorization. Etcd's separate HTTP listener exposes metrics only; its ServiceMonitor deliberately sends no bearer token.

Check the rest of the signal path with:

```promql
ALERTS{alertstate="firing"}
alertmanager_notifications_failed_total{integration="pushover"}
longhorn_backup_target_available{backup_target="default"}
```

The steady state has one `Watchdog` alert from each evaluator in Alertmanager, neither copy routed externally, no firing warning/critical alerts, no Pushover delivery failures, and `longhorn_backup_target_available == 1`.

If either `Watchdog` copy disappears from Alertmanager, treat the absence as an observability incident and check Prometheus/vmalert rule health plus Alertmanager ingestion. A future external dead-man monitor should consume the Watchdog heartbeat and notify through a failure domain independent of this cluster; until then, do not rely on a daily self-notification as proof that the outbound path works.

## Credential rotation

Reset the API token on the Pushover application `k8s-prod Alerts` and replace it in the SOPS-encrypted `alertmanager-config` Secret in the same maintenance window. The Pushover user key normally remains stable, but update it too if the destination account changes. Reconcile kube-prometheus-stack, verify `alertmanager_config_last_reload_successful == 1`, run the end-to-end test above, and consider the old token revoked only after the new notification arrives.
