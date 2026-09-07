# AI/MCP reliability and containment

This runbook records the baseline behind issues #294 and #295, the allowed
dependency graph, and the post-deploy checks for the reliability and network
policy changes.

MetaMCP and MarkItDown were retired in September 2026. MCPHub is the remaining gateway; its configuration and client access model are described in the [MCPHub guide](../../kubernetes/apps/ai/mcphub/README.md).

## Allowed dependency matrix

| Source | Allowed destination | Ports | Reason |
| --- | --- | --- | --- |
| All AI workloads | CoreDNS pods | UDP/TCP 53 | Cluster DNS |
| MCPHub | Nine exact MCP pod identities | Each backend's MCP port | Tool aggregation |
| Grafana MCP | Grafana pods in `observability` | TCP 3000 | Read-only Grafana API |
| LEMON manuals MCP | `lemon-website` pods in `lemon-manuals` | TCP 8080 | Self-hosted manual page retrieval |
| Kubernetes/Flux MCP | Cilium `kube-apiserver` entity | API server ports | Read-only cluster APIs |
| Grafana functional probe | Grafana MCP pods | TCP 8000 | Initialize, tool call, and session cleanup |
| Browser, weather, news, and parts MCPs | Public IPv4 only | TCP 80/443 | Untrusted web/API fetches |

The public-egress rule explicitly excludes pod/Service space, all RFC 1918
space, loopback, link-local, CGNAT, benchmarking, documentation, multicast, and
reserved IPv4 ranges. IPv6 is disabled in Cilium; equivalent exclusions must be
added before enabling it.

MCPHub egress names the exact backend pod identities and ports; each backend's ingress policy admits MCPHub only on that server's MCP port. Cilium requires both policies to allow a connection. When adding a backend, update MCPHub's settings and egress policy together with the backend ingress policy.

## Functional checks

The `grafana-mcp-functional-probe` CronJob runs every five minutes. It uses the
direct Grafana MCP Service URL, initializes MCP, calls `list_datasources`
(which must reach Grafana), verifies the response, and deletes the MCP session.
A TCP-only success cannot satisfy the check. Session-lifetime policy is
intentionally outside this probe.

After rollout:

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

`ai-mcp-reliability` alerts on a failed/stale functional probe or twelve minutes with no probe success metric. `GrafanaMCPFunctionalProbeFailed` and `GrafanaMCPFunctionalProbeMissing` route through VMAlertmanager. vmalert owns external delivery; the temporarily retained Prometheus copy remains local to KPS's null-only rollback Alertmanager. Use the [alerting runbook](alerting.md) to test or troubleshoot the notification path.
