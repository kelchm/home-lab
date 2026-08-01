# AI/MCP reliability and containment

This runbook records the baseline behind issues #294 and #295, the allowed
dependency graph, and the post-deploy checks for the reliability and network
policy changes.

## 2026-07-31 baseline

Live state before the change:

- The MetaMCP pod had restarted 64 times in 38 days. Its latest termination was
  `OOMKilled` at the 1 GiB limit after an 8h40m container lifetime.
- That terminated container created 15 public Streamable HTTP sessions and
  cleaned up none. The replacement had 53 active sessions after 3d15h and used
  about 295 MiB when sampled.
- MetaMCP 2.4.22 contains a five-minute expired-session cleanup loop, but the
  `SESSION_LIFETIME` row was absent, which means infinite retention. Sessions
  were only removed when clients sent MCP `DELETE` requests.
- Each public MetaMCP session creates connections to the configured downstream
  servers. The broken Grafana connection returned `HTTP 403 forbidden: host not
  allowed` during every session setup.
- MetaMCP 2.4.22 is the latest upstream release. The 2.5 tags are an unreleased
  Docker-per-MCP architectural rewrite, so they are not used as a reliability
  fix.

The stable image has no log-level control: its per-session `console.log` calls
are unconditional. Retention bounds both the session population and associated
log volume. Revisit `LOG_LEVEL` when upstream publishes a stable release that
contains it instead of carrying a local image patch.

Containment reconciles the supported DB-backed session lifetime to four hours
during MetaMCP startup, raises its memory request to 512 MiB, and temporarily
raises the limit to 2 GiB for the 14-day soak. The lifetime is maximum age, not
idle age; an MCP client receives a missing-session response after expiry and
must reconnect. Four hours matches the default suggested by MetaMCP's own
settings UI when automatic cleanup is enabled.

## Allowed dependency matrix

| Source | Allowed destination | Ports | Reason |
| --- | --- | --- | --- |
| All AI workloads | CoreDNS pods | UDP/TCP 53 | Cluster DNS |
| MetaMCP | `metamcp-db` pods | TCP 5432 | PostgreSQL |
| MetaMCP | Eight declared HTTP MCP pods | Their single MCP port | Tool aggregation |
| MetaMCP | `10.32.140.1/32` | TCP 443 | `auth.home.kelch.io` Kanidm OIDC through services Traefik |
| Grafana MCP | Grafana pods in `observability` | TCP 3000 | Read-only Grafana API |
| Kubernetes/Flux MCP | Cilium `kube-apiserver` entity | API server ports | Read-only cluster APIs |
| Grafana functional probe | Grafana MCP pods | TCP 8000 | MCP initialize/tool call/delete |
| Browser, document, weather, and parts MCPs | Public IPv4 only | TCP 80/443 | Untrusted web/API fetches |
| Prometheus and vmagent | `metamcp-db` pods | TCP 9187 | CNPG metrics |
| Cluster nodes | `metamcp-db` pods | TCP 8000 | CNPG instance status/lifecycle |
| `metamcp-db` pods | CoreDNS and Cilium `kube-apiserver` entity | DNS/API server ports | CNPG instance lifecycle |

The public-egress rule explicitly excludes pod/Service space, all RFC 1918
space, loopback, link-local, CGNAT, benchmarking, documentation, multicast, and
reserved IPv4 ranges. IPv6 is disabled in Cilium; equivalent exclusions must be
added before enabling it.

`10.32.140.1/32:443` is the only allowed LAN destination. Confirm that
`auth.home.kelch.io` should continue to traverse the shared services Traefik
address; if it moves, update the single CIDR rule in
`metamcp/app/networkpolicy.yaml` before changing DNS.

## Functional checks

The `grafana-mcp-functional-probe` CronJob runs every five minutes. It uses the
exact URL MetaMCP stores, initializes MCP, calls `list_datasources` (which must
reach Grafana), verifies the response, and deletes the MCP session. A TCP-only
success cannot satisfy the check.

After rollout:

```sh
kubectl -n ai wait --for=condition=available deployment/metamcp deployment/grafana-mcp --timeout=5m
kubectl -n ai create job --from=cronjob/grafana-mcp-functional-probe grafana-mcp-manual-probe
kubectl -n ai wait --for=condition=complete job/grafana-mcp-manual-probe --timeout=2m
kubectl -n ai logs job/grafana-mcp-manual-probe
kubectl -n ai logs deploy/metamcp --since=15m | grep -F \
  'Created background idle session for server [grafana]'
```

The final command requires a normal client connection through MetaMCP; it
confirms that the aggregator created Grafana's downstream session without the
old 403.

Expected final log:

```text
Grafana MCP functional probe passed (5 datasources)
```

Check session cleanup and memory without exposing session IDs:

```sh
kubectl -n ai exec deploy/metamcp -- node -e \
  "fetch('http://127.0.0.1:12009/metamcp/health/sessions').then(r=>r.json()).then(x=>console.log({sessions:x.streamableHttpSessions.count,total:x.totalActiveSessions}))"
kubectl -n ai top pod -l app.kubernetes.io/name=metamcp
kubectl -n ai logs deploy/metamcp | grep 'Cleaning up.*expired StreamableHTTP'
```

## Connectivity test matrix

Use a disposable pod carrying the same label as a public-fetch workload. A
successful connection to any negative target is a policy failure.

```sh
kubectl -n ai run ai-egress-test --rm -i --restart=Never \
  --image=docker.io/curlimages/curl:8.16.0 \
  --labels=app.kubernetes.io/name=markitdown-mcp -- \
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
| MetaMCP to each declared MCP and PostgreSQL | Allowed |
| Kubernetes/Flux MCP read calls | Allowed |

Remove a manual probe Job after reviewing it:

```sh
kubectl -n ai delete job grafana-mcp-manual-probe
```

## Alerts and 14-day soak

`ai-mcp-reliability` alerts on a new MetaMCP OOM kill, two restarts within 30
minutes, or a missing/failed Grafana probe for ten minutes. The rules are
selected by both metrics stacks and arrive at the existing kube-prometheus-stack
Alertmanager. That Alertmanager currently has no external notification
integration, so “delivery” means the configured Alertmanager UI until the
observability notifier decision changes.

For issue #294, start the 14-day clock from the MetaMCP rollout. At the end,
record pod age, restart count, last termination reason, session count, current
memory, and the 14-day maximum. Only then remove the temporary 2 GiB limit or
close the no-OOM acceptance item.
