# Log-content alerting evaluation

Evaluated 2026-10-19 against production VictoriaLogs: the Fluent Bit schema from its 11:50 UTC cutover that day, and 7 to 30 days of earlier vlagent rows. Scope: which application log conditions should page, and how LogsQL rules are evaluated and tested. Current operation is in the [logging runbook](../runbooks/logging.md#log-alerts).

## Conclusion

Page on log content only where the log is the only evidence of a failure. Of the candidates, that holds for kanidm, which exports no metrics: replication that keeps failing, and repeated credential denials. Generic per-service error rates, new error patterns and fatal-level records are not paged on: they would have fired on rollouts, upstream DNS blips and controller restarts that either needed no action or already page from metrics.

## Candidates

| Candidate | Evidence | Decision |
| --- | --- | --- |
| Error rate per service against its own baseline | A rule firing at 20 or more errors in 15 minutes and ten times the service's median fired 5 times in 8 hours of Fluent Bit data: three CoreDNS upstream-timeout bursts, one exporter broken pipe during a scrape restart, and one Flux controller restart. Over 7 days of vlagent rows it fired on 4.5 hours of controller-manager "Failed to watch" and on single 15-minute bursts from kube-apiserver and Grafana. Five patterns made up 86% of the day's 11,000 error rows: CSI sidecars watching snapshot CRDs that are not installed, Grafana dashboard conversion, and kanidm `NotAuthenticated` and request timeouts. Changes in flight remove several of them. | Rejected |
| New error patterns (`collapse_nums`) | After the first hour, up to 27 error patterns a service had not logged before appeared every hour, mostly host names in CoreDNS timeouts and rule names in vmalert retries. | Rejected |
| Go panics, `fatal error:`, fatal level | No Go runtime panic or recovered reconciler panic in production in 30 days. Five fatal-level events: two cilium-operator leader-election exits, two Longhorn manager startup conflicts, and an external-dns flag error that crash-looped and is covered by `KubePodCrashLooping`. | Rejected |
| `OOMKilled` text | Not in container logs: the kubelet and kernel record it, and neither journal is collected. No container's last termination reason was `OOMKilled` in the 14 days checked. Only `KubePodCrashLooping` covers repeated OOM kills; a single OOM restart is not alerted from metrics either. | Not a log signal |
| Flux reconcile failures | 35 error rows in 10 hours, all `context canceled` while controllers restarted. `FluxReconciliationFailed` and `FluxReconciliationStalled` read `flux_resource_info`. | Covered by metrics |
| Longhorn and CSI errors | Error rows were the snapshot-CRD watch failures above, which are being fixed separately. Volume, node, disk and backup failures page from Longhorn's metrics; mount failures are kubelet events, not container logs. | Covered by metrics |
| Kanidm replication | Replication health appears nowhere but the log; kaniop's status reports only pod readiness. Kanidm v1.11.2 polls every supplier every 15 seconds and logs each failed poll at error level; a peer whose address no longer resolves logs only warnings. In 30 days, replication errors came only in bursts of a minute or two during restarts, at most 5 per pod. A node outage longer than the alert's 15 minutes can also fire it, alongside `KubeNodeNotReady`, because kanidm can keep a peer's address after the pod becomes unreachable. Kanidm's documentation requires a manual consumer refresh after more than 7 days offline. | `KanidmReplicationFailing` |
| Kanidm rejected sign-ins | 1,528 successful credential checks and no denied, softlocked or unknown-account result in the Fluent Bit rows; one unknown-account result in 30 days of all rows. Kanidm is served only on the internal gateway, so a burst means a lab device guessing credentials, the same reasoning as the honeypot alert. Wrong credentials log `Result::Denied` at error level and `Auth result: Denied` at info; a softlocked account logs only the latter, and an unknown account logs `Auth result: NoMatchingEntries`. | `KanidmSignInsRejected` |

## Mechanism

Both alerts are LogsQL rules in `vmalert-victoria-logs`, scoped by stream filter (`{k8s.namespace.name="identity", service.name="kanidm"}`) and `k8s.cluster.name`. Recording rules into VictoriaMetrics would only be needed for baseline comparisons, which were rejected; that would also need remote write on this vmalert, which it does not have. Every candidate query, including a 7-day scan of all error rows, returned in under 0.2 seconds through the API proxy, so evaluation cost did not decide anything.

Rules live with the application they describe. The VictoriaLogs vmalert therefore selects labelled VMRules from every namespace, as the metrics vmalert does.

## Testing

promtool and `vmalert-tool unittest` evaluate MetricsQL against time-series fixtures only; neither accepts log records. The UniFi LogsQL rules had been checked once against local VictoriaLogs and vmalert binaries and had no CI coverage. `scripts/logging/test-logsql-rules.py` now runs in CI: it requires fixture cases for every LogsQL rule, has vmalert v1.154.0 parse the rule files in dry-run mode, and evaluates each expression with VictoriaLogs v1.53.0's stats query API, the same API vmalert calls, at each case's evaluation time. It checks the returned labels and values and that every label the annotations use is present. A renamed field, a LogsQL syntax error, an annotation naming a missing label and a dropped cluster filter each failed the run. `for` durations are not exercised, so the replication rule expresses persistence inside the expression: with a 15-minute window and `for: 15m`, a two-minute burst would keep the condition true long enough to page after recovery.

## Limits

The Fluent Bit data covered about 10 hours on a day with many rollouts; older rows carry a stored level only where the application emitted one, so the 7-day replay covers the klog-format components. The kanidm denial line comes from the v1.11.2 source and the observed format of its success line (`Handler::Password -> Result::Success`); no denial has been logged here. Revisit generic error-rate alerting if a failure that needed action shows up in the logs before any metric alert.
