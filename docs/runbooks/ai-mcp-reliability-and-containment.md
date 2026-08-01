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

Before rollout, complete both registry gates in the MetaMCP UI:

1. Set **Settings → Session lifetime** to **240 minutes**. This is a supported
   DB-backed setting and is included in CNPG backups.
2. Follow the registry-hygiene procedure in `metamcp-bootstrap.md` to remove the
   package-downloading `time` server and unused `default-endpoint`
   self-reference. MetaMCP initializes every registered server, so inactive
   entries are still dependencies.

The deployment deliberately does not write MetaMCP's private database schema
from a lifecycle hook; the scheduled functional probe continuously checks that
the effective value remains `14400000` ms and alerts on drift.

The lifetime is maximum age, not idle age; an MCP client receives a
missing-session response after expiry and must reconnect. Four hours matches
the default suggested by MetaMCP's settings UI when automatic cleanup is
enabled. The change also raises the memory request to 512 MiB and temporarily
raises the limit to 2 GiB for the 14-day soak.

## Allowed dependency matrix

| Source | Allowed destination | Ports | Reason |
| --- | --- | --- | --- |
| All AI workloads | CoreDNS pods | UDP/TCP 53 | Cluster DNS |
| MetaMCP | `metamcp-db` pods | TCP 5432 | PostgreSQL |
| MetaMCP | Pods labeled `homelab.kelch.io/metamcp-backend=true` | TCP 3000, 3001, 8000, 8080, 8931, 9090 | Explicit opt-in tool aggregation |
| MetaMCP | Kubernetes Service `network/traefik-services` | Effective pod TCP 8443 | `auth.home.kelch.io` Kanidm OIDC |
| Grafana MCP | Grafana pods in `observability` | TCP 3000 | Read-only Grafana API |
| Kubernetes/Flux MCP | Cilium `kube-apiserver` entity | API server ports | Read-only cluster APIs |
| Grafana functional probe | Grafana MCP and MetaMCP pods | TCP 8000 and 12008 | Tool call plus session-policy drift check |
| Browser, document, weather, and parts MCPs | Public IPv4 only | TCP 80/443 | Untrusted web/API fetches |
| Prometheus and vmagent | `metamcp-db` pods | TCP 9187 | CNPG metrics |
| CNPG operator | `metamcp-db` pods | TCP 5432 and 8000 | Reconciliation, failover, and recovery |
| Cluster nodes | `metamcp-db` pods | TCP 8000 | CNPG instance management |
| Same-cluster CNPG pods | `metamcp-db` pods | TCP 5432 and 8000 | Replication and future scale-out |
| `metamcp-db` pods | CoreDNS, same-cluster pods, and Cilium `kube-apiserver` entity | DNS, 5432/8000, API server | CNPG lifecycle |

The public-egress rule explicitly excludes pod/Service space, all RFC 1918
space, loopback, link-local, CGNAT, benchmarking, documentation, multicast, and
reserved IPv4 ranges. IPv6 is disabled in Cilium; equivalent exclusions must be
added before enabling it.

There is no hard-coded LAN CIDR. Cilium selects `traefik-services` by Kubernetes
Service identity and follows its endpoints if the LoadBalancer address changes.
Each downstream MCP opts in at its own controller. When adding a backend, add
the backend label and reciprocal ingress rule alongside that app; update the
small central port union only when introducing a previously unused port.
`playwright-mcp` is intentionally not labeled and denies all ingress because the
checked-in and live MetaMCP inventories use `playwright-stealth-mcp` instead. It
remains deployed only as a dormant comparison/rollback workload.

## Functional checks

The `grafana-mcp-functional-probe` CronJob runs every five minutes. It first
reads MetaMCP's supported public session-lifetime API and requires four hours.
It then uses the exact Grafana MCP URL MetaMCP stores, initializes MCP, calls
`list_datasources` (which must reach Grafana), verifies the response, and
deletes the MCP session. A TCP-only success cannot satisfy the check.

After rollout:

```sh
kubectl -n ai wait --for=condition=available deployment/metamcp deployment/grafana-mcp --timeout=5m
kubectl -n ai create job --from=cronjob/grafana-mcp-functional-probe grafana-mcp-manual-probe
kubectl -n ai wait --for=condition=complete job/grafana-mcp-manual-probe --timeout=2m
kubectl -n ai logs job/grafana-mcp-manual-probe
```

The scheduled check intentionally has no MetaMCP API key. Immediately after
rollout, use an existing private API key to exercise the authenticated
aggregator path with the same probe implementation (the key remains in the
shell environment):

```sh
MCP_SCHEME=https \
MCP_HOST=metamcp.home.kelch.io \
MCP_PORT=443 \
MCP_PATH=/metamcp/default/mcp \
MCP_TOOL_NAME=grafana__list_datasources \
MCP_API_KEY="$METAMCP_API_KEY" \
python3 kubernetes/apps/ai/grafana-mcp/app/probe.py
```

This is the post-rollout acceptance check for MetaMCP database URL correctness,
MetaMCP egress policy, aggregator initialization, Grafana's Host allowlist, and
the Grafana API call. It also sends MCP `DELETE`, so the check does not add
retained sessions.

Expected final log:

```text
Grafana MCP functional probe passed via metamcp.home.kelch.io (5 datasources)
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
| MetaMCP OIDC discovery and interactive login | Allowed through `traefik-services` |
| CNPG operator status/reconcile after instance restart | Healthy |
| Kubernetes/Flux MCP read calls | Allowed |

Remove a manual probe Job after reviewing it:

```sh
kubectl -n ai delete job grafana-mcp-manual-probe
```

## Alerts and 14-day soak

`ai-mcp-reliability` alerts on a new MetaMCP OOM kill, two restarts within 30
minutes, a failed/stale functional probe, or twelve minutes with no probe
success metric. `MetaMCPOOMKilled`, `MetaMCPRepeatedRestarts`,
`GrafanaMCPFunctionalProbeFailed`, and `GrafanaMCPFunctionalProbeMissing` are
currently UI-only alerts: both metrics stacks select the rules, but the existing
kube-prometheus-stack Alertmanager has no external notifier. Prioritize the
observability notifier decision so these alerts can page proactively; until
then, delivery only means visibility in the Alertmanager UI.

For issue #294, keep the issue open until the authenticated MetaMCP probe above
passes and start the 14-day clock from the rollout. At the end, record pod age,
restart count, last termination reason, session count, current memory, and the
14-day maximum. Only then remove the temporary 2 GiB limit or close the no-OOM
acceptance item.
