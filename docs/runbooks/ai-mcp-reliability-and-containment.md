# AI/MCP reliability and containment

This runbook records the baseline behind issues #294 and #295, the allowed
dependency graph, and the post-deploy checks for the reliability and network
policy changes.

MetaMCP and MarkItDown were retired in September 2026. MCPHub is the remaining gateway; its configuration and client access model are described in the [MCPHub guide](../../kubernetes/apps/ai/mcphub/README.md).

## Allowed dependency matrix

| Source | Allowed destination | Ports | Reason |
| --- | --- | --- | --- |
| All AI workloads | CoreDNS pods | UDP/TCP 53 | Cluster DNS |
| MCPHub | Eight exact MCP pod identities | Each backend's MCP port | Tool aggregation |
| Grafana MCP | Grafana pods in `observability` | TCP 3000 | Read-only Grafana API |
| LEMON manuals MCP | `lemon-website` pods in `lemon-manuals` | TCP 8080 | Self-hosted manual page retrieval |
| Kubernetes/Flux MCP | Cilium `kube-apiserver` entity | API server ports | Read-only cluster APIs |
| Grafana functional probe | Admin Traefik pods in `network` | TCP 8443 (Service 443) | HTTPS gateway, scoped bearer auth, tool call and session cleanup |
| Weather, news, and parts MCPs | Public IPv4 only | TCP 80/443 | Untrusted web/API fetches |

The public-egress rule explicitly excludes pod/Service space, all RFC 1918
space, loopback, link-local, CGNAT, benchmarking, documentation, multicast, and
reserved IPv4 ranges. IPv6 is disabled in Cilium; equivalent exclusions must be
added before enabling it.

MCPHub egress names the exact backend pod identities and ports; each backend's ingress policy admits MCPHub only on that server's MCP port. Cilium requires both policies to allow a connection. When adding a backend, update MCPHub's settings and egress policy together with the backend ingress policy.

## Functional checks

The `grafana-mcp-functional-probe` CronJob runs every five minutes through `https://mcphub.home.kelch.io/mcp/homelab-read`, following the admin gateway's HTTPS route into MCPHub. It initializes MCP, calls `grafana__list_datasources` (which must reach Grafana), validates the datasource list and deletes the MCP session when one was issued. Its own SOPS Secret supplies a dedicated system bearer key scoped to `homelab-read`; a key-specific tool policy permits only Grafana's `list_datasources`. It cannot reach the Grafana MCP backend directly. A TCP-only success cannot satisfy the check. Session-lifetime policy is intentionally outside this probe.

Changing MCPHub's settings Secret triggers Stakater Reloader and a single-pod `Recreate` rollout; clients briefly lose their in-memory sessions and must reconnect. The new key and tool policy leave existing principals and groups unchanged. After rollout, wait for the next scheduled probe to succeed and confirm existing clients can still list their normal tools. The new key and in-cluster gateway policy need this post-merge acceptance; a workstation check cannot prove pod egress.

### Merge window and alert timing

Flux applies the probe CronJob/Secret and the MCPHub settings through separate Kustomizations; MCPHub depends on grafana-mcp, but the CronJob scheduler does not wait for the new gateway key to load. After the settings Secret changes, Stakater Reloader asynchronously restarts MCPHub using its single-pod `Recreate` strategy. There is no guaranteed restart duration or ordering relative to a probe run. Merge just after a successful scheduled run if practical, leaving almost five minutes before the next run at wall-clock minutes :00, :05, :10, and so on. Do not assume a ten-minute grace period.

- **Failed Job branch, critical:** an unsuccessful Job with failed pod count greater than zero, no success for that same Job, and a start time less than ten minutes ago starts a two-minute `for` timer. It can fire about two minutes after the failure is first scraped, even when the previous run succeeded recently. Each Job permits one retry and has a 120-second total deadline. A subsequent scheduled Job's success does not clear an older failed Job's branch; that branch remains eligible until ten minutes after the failed Job's own start time or until its metrics disappear.
- **Stale success branch, critical:** the same two-minute `for` timer starts when vmalert first observes a last-success timestamp older than ten minutes. With uninterrupted evaluation across that threshold, firing is just over twelve minutes after the last success. If a newly loaded rule or newly observed series is already stale, firing can instead begin about two minutes after that first observation, regardless of how old the success is. A timer already pending before merge retains its elapsed time; merge does not grant a fresh two-minute hold. A fresh success resets this branch.
- **Missing success metric, warning:** twelve continuous minutes without a last-success series fires the missing alert. A failed first-ever run can still trigger the critical failed-Job branch before this warning.

These times precede scrape/evaluation and notification delays; the metrics evaluator runs every 30 seconds and Alertmanager normally waits 30 seconds before a group's first notification. Timers already pending before merge do not acquire a new grace period.

For the quickest diagnosis, compare the probe Job's logs and start time with MCPHub's pod replacement/readiness and the CronJob's `lastSuccessfulTime`. A 502/connection failure during the replacement gap followed by a successful scheduled run indicates a recovered restart interruption; the older failed Job can still keep its critical branch firing for the remainder of its ten-minute window. A repeated failure after the replacement pod is Ready needs investigation: 401/403 suggests key loading or tool authorization, 404 suggests the route/tool name, and DNS/TLS/timeouts suggest name resolution or the network path. Read Job events for scheduling or missing-Secret failures. Confirm existing clients reconnect and still list their normal tools. Do not infer recovery merely from the gateway pod being Ready: the functional check must succeed.

Read-only commands for that window (add the explicit main-checkout `--kubeconfig` path when running from a T3 worktree). Prefer the newest retained Job with failed pods and no success, falling back to the newest probe Job when none matches, so a later success does not hide the failure behind an active alert:

```sh
kubectl -n ai get pods -l app.kubernetes.io/name=mcphub -o wide
kubectl -n ai get cronjob grafana-mcp-functional-probe -o yaml
kubectl -n ai get jobs --sort-by=.metadata.creationTimestamp
PROBE_JOB=$(kubectl -n ai get jobs -o json | jq -r '
  [.items[] | select(any(.metadata.ownerReferences[]?;
    .kind == "CronJob" and .name == "grafana-mcp-functional-probe"))]
  | map(select((.status.failed // 0) > 0 and (.status.succeeded // 0) == 0)) as $failed
  | (if ($failed | length) > 0 then $failed else . end)
  | max_by(.metadata.creationTimestamp) | .metadata.name // empty')
if [ -n "$PROBE_JOB" ]; then
  kubectl -n ai logs "job/$PROBE_JOB" --all-containers=true --prefix=true
  kubectl -n ai describe job "$PROBE_JOB"
else
  printf '%s\n' 'No retained functional-probe Job was found.'
fi
```

After rollout, an optional manual check follows the existing procedure:

```sh
kubectl -n ai wait --for=condition=available deployment/mcphub deployment/grafana-mcp --timeout=5m
kubectl -n ai create job --from=cronjob/grafana-mcp-functional-probe grafana-mcp-manual-probe
kubectl -n ai wait --for=condition=complete job/grafana-mcp-manual-probe --timeout=2m
kubectl -n ai logs job/grafana-mcp-manual-probe
```

## Connectivity test matrix

Use a disposable pod carrying the same label as a public-fetch workload. A
successful connection to any negative target is a policy failure.

```sh
kubectl -n ai run ai-egress-test --rm -i --restart=Never \
  --image=docker.io/curlimages/curl:8.16.0 \
  --labels=app.kubernetes.io/name=open-meteo-mcp -- \
  sh -eu -c '
    curl -fsSI --connect-timeout 5 https://example.com >/dev/null
    ! nc -z -w 3 10.43.0.1 443
    ! nc -z -w 3 10.32.30.8 6443
    ! nc -z -w 3 10.32.25.5 2049
  '
```

| Test | Expected |
| --- | --- |
| Public HTTPS (`example.com`) | Allowed |
| Unrelated ClusterIP (`10.43.0.1`) | Blocked |
| Node/management API VIP (`10.32.30.8`) | Blocked |
| Storage/NAS address (`10.32.25.5`) | Blocked |
| Scheduled Grafana MCP tool call | Allowed |
| MCPHub to each configured backend | Allowed |
| Kubernetes/Flux MCP read calls | Allowed |

Remove a manual probe Job after reviewing it:

```sh
kubectl -n ai delete job grafana-mcp-manual-probe
```

## Alerts and memory monitoring

`ai-mcp-reliability` reports a failed/stale gateway check or twelve minutes with no probe success metric. `GrafanaMCPFunctionalProbeFailed` remains `critical` and `GrafanaMCPFunctionalProbeMissing` remains `warning`, using their existing Pushover routes. Inspect the scheduled Job logs, bearer Secret, gateway route and MCPHub backend connection when one appears.
