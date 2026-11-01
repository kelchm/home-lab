# Alert Delivery and Testing

Operational procedure for VMAlertmanager in `observability`, including ownership, routing, silences, delivery tests, and control-plane coverage checks.

## Ownership and routing

The homelab operator owns every alert. `critical` means act as soon as the notification arrives; `warning` means act, but not urgently. Alerts without one of those severities stay in Alertmanager for inspection but do not notify externally.

Alertmanager routes as follows:

| Match | Receiver | Repeat | Resolution |
|---|---|---:|---|
| `severity="critical"` and `evaluator="vmalert"` | `k8s-prod Alerts` in Pushover, high priority | 12 hours | Quiet priority |
| `severity="warning"` and `evaluator="vmalert"` | `k8s-prod Alerts` in Pushover, normal priority | 12 hours | Quiet priority |
| `alertname="Watchdog"` and `evaluator="vmalert"` | `healthchecks-watchdog` webhook to healthchecks.io | 1 minute | None |
| Everything else | `null` | N/A | N/A |

vmalert is the delivery authority and the cluster's only rule evaluator. It runs as two instances because each reads one datasource: `vmalert-victoria-metrics-k8s-stack` evaluates MetricsQL against VMSingle, and `vmalert-victoria-logs` evaluates LogsQL against VictoriaLogs. VMRules labelled `alerting.home.kelch.io/datasource=victorialogs` load only into the second; every other rule loads only into the first. [Log alerts](logging.md#log-alerts) covers when to write one. Both use the notifier configuration from `vmalert.additionalNotifierConfigs`, which adds `evaluator=vmalert` to every alert they send to VMAlertmanager. The label exists only on notifications: vmalert does not write it to `ALERTS` or recording-rule output, so joins between raw and recorded series need no `ignoring(evaluator)`. `cluster` comes from each rule group, as described in [Cluster labels](#cluster-labels). The SOPS-encrypted `vmalertmanager-config` Secret owns VMAlertmanager routing, the Pushover application token/user key, and the healthchecks.io ping URL. Do not put any of these credentials in Helm values, shell history, issue comments, or screenshots.

Use a source-specific Pushover application for each independent alert producer. Kubernetes uses `k8s-prod Alerts`; a separate Proxmox alert producer should use a separate application such as `pve-prod Alerts` rather than sharing this token. PVE backup completion notifications currently enter as metrics through the existing vmagent and are evaluated by the same vmalert producer, so their alerts reuse this application and watchdog. Both applications can deliver to the same Pushover user and devices while retaining distinct names, icons, quotas, audit history, and revocation boundaries.

Firing critical alerts use Pushover priority `1`, which bypasses quiet hours but does not create emergency acknowledgements. Firing warnings use priority `0`; resolved notifications use quiet priority `-1`. Emergency priority `2` remains disabled until retry and acknowledgement behavior has been deliberately designed.

## Alert conventions

Rules follow the [monitoring-mixins guidelines](https://monitoring.mixins.dev/#guidelines-for-alert-names-labels-and-annotations), which the bundled kubernetes-mixin rules also follow:

- **Name:** a terse description of the condition, in CamelCase, starting with a capital. Related alerts share their first component, such as `UniFi…` or `Longhorn…`.
- **Severity:** `critical` when something that should be up is down, or something is at risk of failing or of damage, and nothing else is covering for it. `warning` when it needs action but not urgently: lost visibility, a failed or stale backup, a change that did not apply, or a failure that something else is covering for, such as one WAN of two, which escalates to `critical` if it lasts. `info` marks something out of the ordinary that needs no action by itself. Routing above turns these into a high-priority push, a normal push and no notification; Home shows `info` alerts as worth a look.
- **Annotations:** every rule has a `summary`, one readable sentence, and a `description` with the detail templated in. `runbook_url` is optional.

The guidelines leave some choices open. The lab settles them this way:

- **First component is the thing affected,** not the machine or collector that observes it. A UPS is `UPS…` whether a Pi or the NAS reads it, and a blackbox probe is `Probe…` though a Pi runs it.
- **Machine conditions use `Node…`** for any machine, as the upstream node-exporter alerts do; `KubeNode…` is the Kubernetes node object. An upstream name is reused only when the condition and threshold match it; otherwise the lab name must not collide with it, as `NodeFilesystemSpaceLow` and `NodeLinkFlapping` do not collide with upstream `NodeFilesystemAlmostOutOfSpace` and `NodeNetworkInterfaceFlapping`.
- **Acronyms are capitals** as upstream writes them (`KubeAPI…`, `NodeCPU…`): CPU, GPU, NAS, UPS, PVE, API, WAN, DHCP, OOM, ATA, RAID. A project's own spelling is kept: UniFi, etcd, NVMe, GLKVM, TMobile.
- **One word per meaning:** `Down` when something that should answer does not; `MetricsMissing` when telemetry about the thing has stopped arriving; `IngestionSilent` when a log stream has stopped; `TemperatureHigh` and `TemperatureCritical`; `SpaceLow` and `SpaceCritical` for capacity; `-ed` for an event that has happened (`LonghornBackupFailed`) and `-ing` for a condition that continues (`KanidmReplicationFailing`). A state named by the system it comes from keeps that name, such as Kubernetes and Longhorn `NotReady` or Longhorn's `LonghornBackupTargetUnavailable`.
- **One condition, one name.** Rules for the same condition on different sets of machines share a name, and their labels tell them apart: `UPSOnBattery` covers the UPSs that Pis read over NUT and the one the NAS reads over SNMP. A rule's thresholds may differ by machine when its description says so.
- **Names defined upstream keep their spelling:** every bundled rule, chart-provided rules such as CloudNativePG's `CNPGClusterOffline` and smartctl-exporter's `SmartCTL…`, and rules copied from upstream unchanged.
- **The thing an alert is about is a label:** `node` for a Kubernetes node, `instance` with the hostname for any other machine, and `cluster` where the series has one.

`scripts/ci/validate-alert-conventions.py` runs in CI and checks every alert rule under `kubernetes/`: the name's form, a severity of `critical`, `warning` or `info`, both annotations, and that no rule brings back a name retired when these conventions were adopted.

## One alert per machine

A machine that goes down raises one notifying alert, whatever the machine is. While it fires, Alertmanager holds back the alerts that only say the same machine is unreachable or that its data has stopped arriving. A fault the machine still reports, such as a temperature or a SMART error, still notifies, and so does anything the outage breaks: pods that cannot run, degraded volumes, failed backups, service probes. The hold-back rules are the `VMAlertmanagerConfig` [one-alert-per-machine](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/one-alert-per-machine.yaml). The VMAlertmanager sets `disableNamespaceMatcher` so they apply to alerts from every namespace, including host alerts, which have none.

| Machine | Its alert | Held back while it fires |
|---|---|---|
| k8s-prod node | `KubeNodeDown`, critical: vmagent has not reached the node's kubelet for four minutes and the node is not cordoned | Its `…MetricsMissing` and `…SensorMissing` alerts; `KubeNodeNotReady`, `KubeNodeUnreachable`, `KubeNodeReadinessFlapping`, `KubeletInstanceUnreachable`, `LonghornNodeNotReady` and `FluentBitCollectorTargetMissing` for it; its API server, controller-manager and scheduler instances, named by its address |
| PVE node | `PVENodeDown`, critical, after five minutes | Its `NodeMetricsMissing`, `…MetricsMissing`, `…SensorMissing` and `NodeCollectorDown` alerts, and `PVEGuestDown` for guests assigned to it |
| Spark, NAS, Pi | `NodeMetricsMissing`, warning, after ten minutes without metrics | Its `…MetricsMissing`, `…SensorMissing` and `NodeCollectorDown` alerts. `UPSMetricsMissing` fires only while its Pi still reports |

The rules match an alert to the machine by its `instance` (the hostname, which `KubeNodeDown` and `PVENodeDown` also carry) or `node` label, and only for these alerts. A machine that only looks down can still be running: a kubelet that stops answering while node-exporter reports a critical temperature is the likely case, so a fault the machine reports is never held back. A Longhorn volume alert names the node a volume is attached to and is about the volume, so it is not held back either. The naming conventions keep this list short: alerts about missing data share the `MetricsMissing` and `SensorMissing` endings.

A hold-back works only if the down alert is already firing when the other alert would first notify, so each down alert waits less than the alerts it holds back. `KubeNodeDown` reads vmagent's scrape of the kubelet rather than kube-state-metrics, which runs as one replica and may be on the node that died; at four minutes it comes before `LonghornNodeNotReady` and `FluentBitCollectorTargetMissing` at five and the node and kubelet alerts at fifteen. `PVENodeDown` at five minutes comes before the ten of `NodeMetricsMissing` and `PVEGuestDown`. Alertmanager also waits 30 seconds before a group's first notification. When the down alert resolves, anything still firing about that machine notifies at the group's next interval; a symptom that clears within it does not.

A planned reboot cordons the node, so `KubeNodeDown` skips it, and the alerts it would hold back notify as they would without it: a reboot that hangs past five minutes notifies through `LonghornNodeNotReady`.

`scripts/ci/test-alert-inhibition.py` runs these rules in Alertmanager in CI and checks, for a k8s-prod node, a PVE node and the NAS going down, which simulated alerts are held back and which still notify.

Two aggregate alerts about a k8s-prod node are not held back, because neither names the machine: `TargetDown` counts each scrape job's failed targets, and `etcdMembersDown` counts etcd members. A node that stays down past ten minutes therefore also raises `TargetDown` for each namespace with a per-node job, and past twenty, `etcdMembersDown`.

Alertmanager groups alerts by name, cluster and namespace, not by machine: two machines that go down together arrive in one notification, a second machine joining a firing group sends an update after `group_interval`, and a firing group repeats every 12 hours and announces its resolution.

A problem that only affects what can be seen of a device is `info` when every notifying rule it blinds is covered by another notifying alert, and notifies otherwise. `TMobileMetricsMissing` is info: no notifying rule reads the T-Mobile gateway, because `UniFiWANDown` reports a WAN1 outage. `StarlinkMetricsMissing` notifies, because `StarlinkObstructed` and `StarlinkPingDropSustained` have no other cover; so do `UniFiMetricsMissing`, `PVEMetricsMissing`, `NodeCollectorDown` and the Longhorn, Flux and cert-manager `MetricsMissing` alerts.

The bundled `RequestErrorsToAPI` and `TooManyLogs` are `info`: they fire on any API error or `warn` log that recurs for 15 minutes, and repeated cancelled client queries, such as overlapping Grafana refreshes, are enough to trip them. A real fault can trip them too, so read `RequestErrorsToAPI`'s `path` label or the component's warnings before dismissing one. `TooManyErrorLogs`, `ServiceDown` and `TooManyRestarts` still notify for a failing component.

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

To test the critical route, repeat with `severity: critical` and expect a high-priority notification instead.

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
            severity: warning
            test: synthetic
          annotations:
            summary: Synthetic Longhorn stale-backup delivery test
            description: This alert is expected during an operator-initiated end-to-end test.
EOF
```

Confirm one normal-priority Pushover notification, then clean up and confirm a quiet-priority resolution:

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
