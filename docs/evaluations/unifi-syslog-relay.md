# UniFi CEF relay qualification

On October 18, 2026, a stock vlagent v1.53.0 relay passed isolated loopback qualification against VictoriaLogs v1.53.0. This supports the narrow dedicated relay; it does not require changing the Kubernetes collector or replacing it with Vector. Production still used direct UDP ingestion during this work. [#856](https://github.com/kelchm/home-lab/issues/856) tracks live acceptance; the [logging runbook](../runbooks/logging.md#relay-qualification-and-transport-cutover) defines cutover and rollback.

## Why JSONLine

Latest release was v1.53.0 (October 1); master `2721f2111df153f58fecdd8955641c35baaa2d10` still hardcoded `msgFields = []string{"message"}` in [`syslog.go`](https://github.com/VictoriaMetrics/VictoriaLogs/blob/2721f2111df153f58fecdd8955641c35baaa2d10/app/vlinsert/syslog/syslog.go). This is an upstream-reported problem, with root-fix discussion in [#1362](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1362#issuecomment-4350319170). The parent experiment reproduced the duplicate `_msg` placeholder when JSON forwarding retained its default, and confirmed native forwarding ignores message-field headers.

The qualified path disables that placeholder with an actual empty CLI value, sends JSONLine, and promotes `cef.extension.msg,_msg,cef.name` in order. Promotion renames the selected field; it does not copy. Normal CEF consumes `cef.extension.msg`; missing-message CEF consumes `cef.name`. Other fields and the explicit original stream identity survive. Conditional `format` handles retained rows without destroying new `_msg`; WAN/failover/honeypot alerts cover both schema generations and the name fallback.

## Reproduce

Use official checksum-verified v1.53.0 binaries for your platform; this run used the upstream Darwin arm64 release. The container is pinned separately to the multi-platform v1.53.0 image digest. The script records binary hashes and versions, sanitizes addresses/paths from the actual Deployment args to fresh loopback ports and scratch directories, and rejects external HTTP targets.

```sh
python3 scripts/logging/qualify-unifi-relay.py \
  --bin-dir /path/to/verified/binaries \
  --manifest kubernetes/apps/observability/unifi-syslog/app/deployment.yaml \
  --output /tmp/unifi-relay-qualification
```

The default binary/result paths point to the original local research artifacts; override them elsewhere. No Kubernetes writes, notifications, force-flush or force-merge requests are used. Raw synthetic rows, exact runtime args, per-rule results, binary hashes and queue counters remain in `/Users/kelchm/.t3/artifacts/cef-relay-qualification-final/results.json` on the qualification host; this file is a portable summary of those results.

## Results

| Check | Result |
| --- | --- |
| Message/field fixtures | 21/21 passed: WAN down/restored/failover, IDS, client/device/admin, UniFi OS, unknown class, missing-message name fallback, malformed CEF, ordinary gateway RFC3164 dnsmasq/kernel and RFC5424 process ID. |
| Timestamps/sender/streams | Receiver arrival `_time`, retained historic header `timestamp`, sender `remote_ip`, event fields and exact direct-baseline `_stream`, including `proc_id`, passed. |
| Immediate query | All fixture rows queryable within 2.919 seconds using normal backend ingestion, without flush/merge. |
| Historical compatibility | Literal repository WAN, IDS and honeypot expressions returned equal direct/relay results; conditional `format` retained every new `_msg`. Old extension-only WAN expression correctly failed new schema. |
| Backend outage and graceful restart | HTTP proxy returned 503; relay received 128 unique IDs, exited on SIGTERM with status 0, restarted against the same queue while still unavailable, then delivered all 128 after recovery. Missing 0, duplicates 0, unexpected 0. |
| Persistence and health | 560,297 bytes on disk, 560,211 payload bytes reread; pending queue drained, drop counters 0; HTTP 503 retries observed. |

The supplied binary hashes were `1d6a5896ad33da2066f2e9ea9f3260ab2de483847745758eb35455a9757de9fa` (VictoriaLogs) and `84e9881155de18acf0044a994d58e4d4c8e2de0408a4ff012f5d47fb32b0fef1` (vlagent). Both reported v1.53.0, commit `915d91904b`. Finite recovery and zero duplicates in this run do not establish exactly-once delivery.

This is synthetic qualification on Darwin, not production validation of the Linux container, Service routing, Cilium source matching, Longhorn attachment or alert delivery. Abrupt termination/power loss, queue overflow, UDP saturation and node/storage failover were not tested. Disk buffering starts only after reception and may evict old blocks at capacity. Live acceptance must use the procedure and remain open in #856 until observed.
