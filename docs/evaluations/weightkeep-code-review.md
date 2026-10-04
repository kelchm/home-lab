# WeightKeep code review

> **Status, October 4, 2026.** This is the September 30 review, kept as written below the line. "Current implementation" and "current release" mean the reviewed commit, not the project today; later upstream changes were not examined. The path forward it describes is the set of gates that would apply if WeightKeep were revisited. That hardening was not started and is not planned. The current position is in the [main evaluation](hugging-face-nas-cache.md#current-position).

---

Reviewed September 30, 2026, at upstream main commit [`933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3`](https://github.com/afshinghezeli/weightkeep/tree/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3). The review covers the HF proxy, acquisition, content store, manifests, GC, verification, exports, torrent handling, sharing policy, registry, CLI wiring, and tests. The intended workload is a persistent, multi-client NAS archive accessed through `HF_ENDPOINT`.

## Assessment

WeightKeep has a useful architecture for this workload, but the current implementation is not ready for deployment as the shared NAS archive. The earlier evaluation established content reuse and a feasible importer; the deeper review found failures in security boundaries, offline timeout handling, retention, and exports. Those findings materially increase the scope beyond adding an importer and a verified-serving flag.

The most serious defects are unauthenticated forwarding of upstream write operations with the server's token, filesystem traversal during torrent ingestion, delivery of corrupt resumed files, and approval of private repositories for public seeding. The archive also loses availability on upstream metadata timeouts and can garbage-collect content it still needs.

This is a source review with local reproductions, not a production penetration test. No real upstream write operation, private-model download, public seed, or NAS deployment was performed. The reproductions use fake credentials, local servers, and isolated temporary files. High severity below means a concrete confidentiality, integrity, destructive-write, or archive-availability failure; medium means a narrower failure or a misleading protection claim.

## Confirmed findings

### 1 High severity Upstream writes are delegated to unauthenticated clients

[`internal/proxy/api.go:316`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/proxy/api.go#L316) passes unknown `/api/` requests upstream with the incoming HTTP method and body. [`internal/hub/client.go:125`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/hub/client.go#L125) discards incoming authorization and adds the server's configured HF token for the upstream host. There is no client authorization check on this route.

An unauthenticated local `DELETE /api/repos/delete` with a repository JSON body reached a fake upstream as DELETE, with the server's token and body intact, and returned HTTP 200. A real upstream operation would depend on that token's permissions. A read-only token reduces destructive authority but still exposes its read authority through other API routes.

The CLI warns that listeners beyond localhost expose cached models. That warning does not describe the additional delegation of upstream write authority. Even localhost binding allows other reachable local processes to use the token through this path.

Fix direction: allow only explicitly supported read endpoints and methods, plus the known read-only `paths-info` POST. Reject arbitrary mutating passthrough. Decide separately how LAN client access is authorized; a shared HF token is not a client authorization mechanism.

### 2 High severity Torrent storage writes before validating paths

[`internal/torrent/seed.go:302`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/torrent/seed.go#L302) constructs filesystem paths from the torrent's name and file paths. It creates empty files with `os.WriteFile`, which truncates existing files. The embedded WeightKeep manifest is validated later, after the torrent storage has been opened in `Client.Fetch`.

A malformed, zero-length torrent containing `../../sentinel` truncated an existing file outside the ingest directory. WeightKeep then rejected the torrent because it had no embedded WeightKeep manifest. Rejection therefore occurs after the destructive effect. This reproduction used the actual torrent client and metadata-loading path, with DHT and web seeds disabled; it required no peer payload or network download.

Fix direction: validate torrent name, all effective file paths, lengths, and the embedded manifest before opening writable storage. Cover UTF-8 alternate names and paths too. Use filesystem containment for all writes, and refuse to truncate existing unrelated files. A valid info hash authenticates a torrent's contents; it does not establish that its paths are safe.

### 3 High severity Streaming verification does not protect client integrity

[`internal/proxy/resolve.go:182`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/proxy/resolve.go#L182) sends bytes before full-file verification and holds back only the last byte of the complete file. An early range can finish before that byte is needed. A client can also retain bad partial bytes and resume from a later offset after the server refetches correct content.

The preceding [qualification](hugging-face-cache-followup.md) reproduced both cases: an early range returned 206 with corrupt bytes, and real `huggingface_hub` 2.0.0 returned a successfully completed 26,738,688-byte file with the wrong SHA-256 after automatic resume. The server's final stored blob was correct; the client's assembled file was not. The stock tampered-download test checks one uninterrupted full response and misses these cases.

Fix direction: serve only fully verified blobs, or introduce a genuinely authenticated chunk-verification scheme before releasing chunks. The verified-before-serving prototype closes the tested failure, but increases cold time to first byte. The real SDK recovered from a 12-second stall with its 10-second timeout without a second upstream fetch; much longer cold acquisitions still need qualification.

### 4 High severity Private repositories can be approved for default seeding

[`internal/keep/pull.go:264`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/keep/pull.go#L264) preserves gating and license metadata in the manifest but drops `RepoInfo.Private`. [`internal/keep/licence.go:20`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/keep/licence.go#L20) therefore never passes the current repository's private status to the policy engine, even though that engine supports it.

The fixture pulled a repository whose API response said `private:true`, with a valid MIT license and a model file. The private flag survived in the archived raw info JSON. Nevertheless, the default seed plan classified the revision as tier A and approved sharing without an opt-in. No public upload was performed. The fake Hub originally always emits `private:false`, which explains why existing end-to-end policy tests miss this path.

Fix direction: durably retain private status and propagate it into sharing decisions. Treat missing status conservatively, including older manifests and torrent-origin revisions. Loading saved raw info can support a migration, but relying only on a fresh network check would weaken offline policy behavior.

### 5 High severity Cached models fail when upstream metadata times out

[`internal/proxy/server.go:145`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/proxy/server.go#L145) falls back to a local revision only when `hub.Retryable` is true. [`internal/hub/errors.go:112`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/hub/errors.go#L112) makes any error matching `context.DeadlineExceeded` non-retryable. This includes the HTTP client's metadata timeout, not just abandonment by the caller.

After warming a model, the fixture expired its `main` ref and made upstream metadata exceed a shortened API timeout. The subsequent request returned 504 while the weight was still in the store. Immediate upstream 503 responses take the fallback path and succeed, so the earlier outage results remain valid but did not cover this failure. Explicit offline mode and already-kept commit pins avoid this metadata refresh path.

Fix direction: distinguish caller cancellation from the service's upstream timeout. An upstream timeout should permit local fallback. Bound online ref refresh latency so fresh clients do not wait through long upstream failure windows before getting cached content.

### 6 High severity GC can delete content still needed by the archive

There are three reproduced variants of the same missing retention coordination.

First, [`internal/manifest/index.go:25`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/manifest/index.go#L25) publishes the manifest file before its SQLite transaction. A failed or canceled indexing step leaves an authoritative, readable manifest without database references. [`internal/keep/gc.go:42`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/keep/gc.go#L42) derives reachability only from SQLite. In the fixture, a canceled save left a manifest loadable from disk, but GC deleted its recorded blob after the grace period. Reindexing or recovery from manifest files is not wired into startup or a recovery command.

Second, a pull saves its manifest only after all selected downloads finish. GC protects unreferenced new blobs for one hour, described in code as long enough for any pull. The fixture stalled one file in an active pull and aged an already-finished config beyond that grace; GC deleted the config while the pull was still running. Aging represents a long acquisition without waiting two hours. Large multi-file NAS pulls can realistically exceed the fixed grace period.

Third, generated Ollama config/template/parameter blobs are put into the store without references in the revision's `files` table. After warming an Ollama pull and aging its generated config, GC deleted that config even though the cached Ollama manifest still referenced it. An offline HEAD for the config then returned 404. HF revision manifests are not the only durable roots this service needs to retain.

Fix direction: make published manifests and active acquisitions explicit GC roots, reconcile file manifests with their database index, and coordinate publication with GC. A larger grace period is only a mitigation. Recovery from a missing database must not permit GC to destroy the retained archive.

### 7 Medium severity Export accepts same-size corrupt files as matching

[`internal/hfcache/hfcache.go:158`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/hfcache/hfcache.go#L158) treats any existing destination with the expected length as already present. It never checks the content hash. The same helper is used by native-cache and plain-directory exports. Existing regular snapshot files can also bypass relinking without a content check.

Exporting a verified four-byte `GOOD` blob onto an existing four-byte `EVIL` file returned success and counted it as existing. The wrong bytes remained. Avoiding overwrite of independently created files is a reasonable policy, but claiming they match because their sizes match is incorrect.

Fix direction: verify existing content against the manifest and fail clearly on mismatch. Preserve the no-overwrite policy unless replacement is explicitly selected. Exports should not silently bless corrupt bytes merely because the store's source is verified.

### 8 Medium severity Export follows destination directory symlinks

[`internal/hfcache/hfcache.go:120`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/hfcache/hfcache.go#L120) builds destinations using `filepath.Join` and ordinary filesystem calls. Lexical validation of manifest paths does not prevent existing parent-directory symlinks from escaping the destination.

With `destination/sub` symlinked to a separate temporary directory, exporting `sub/model.bin` wrote into that outside directory successfully. The fixture used a valid manifest path and copy mode. This requires preexisting destination state, unlike the torrent traversal, which comes entirely from the supplied torrent.

Fix direction: constrain destination operations through a directory root and define which existing symlinks are permitted. Native HF snapshot symlinks are intentional; parent-directory escapes are not. The OMS verifier already uses `os.Root` for contained file access, so the repository has an applicable pattern.

### 9 Medium severity Registry protections are advertised but not connected

[`internal/cli/registrycmd.go:43`](https://github.com/afshinghezeli/weightkeep/blob/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3/internal/cli/registrycmd.go#L43) says a synced registry makes pull cross-check records, supplies magnet fallback, and makes seed honor the denylist. Call-site review finds `openRegistry` used by registry sync/show operations, not ordinary pull or seed. The keeper has no registry dependency; `SeedPlan` does not load a denylist; torrent pulling does not compare against a signed registry record.

The registry client itself has tests for signature mismatch, tampered targets, rollback, and expiry. Those tests do not establish integration with acquisition and sharing. The roadmap also leaves namespace cross-checking unfinished. This is a verified wiring and documentation mismatch, not a demonstrated cryptographic break in go-tuf.

The subsequent upstream issue-planning pass found this work already in open PRs: [#30](https://github.com/afshinghezeli/weightkeep/pull/30) proposes denylist enforcement, and [#31](https://github.com/afshinghezeli/weightkeep/pull/31) proposes pull cross-checking and registry magnet fallback. Their descriptions overlap this finding; their implementations were not qualified in this review. Track those changes rather than file a duplicate feature issue. The finding above describes the reviewed main commit.

Fix direction: accurately describe current behavior immediately. Before promising those protections, connect registry records and denylist decisions to the actual pull and seed entry points and test the end-to-end refusal cases. A torrent info hash authenticates what its publisher supplied; it does not independently bind that content to HF provenance.

## Architecture and implementation quality

The separation between Hub protocol, fetcher, store, manifests, and CLI is understandable. Blob acquisition uses bounded buffers rather than loading weights into RAM. The store verifies expected content hashes and sizes before publishing blobs, fsyncs files, and uses rename for publication. Plain SHA-256-addressed files are recoverable without decoding a private chunk format. These are real advantages for the NAS workload.

Global blob identity and download coordination are the right granularity. The previous qualification demonstrated reuse across repository names and revisions, including concurrent clients. The proxy deliberately removes Xet signals so clients cannot bypass the NAS. Durable commit manifests and refs support offline lookup; the timeout defect limits the online-fallback path rather than invalidating the storage model.

Token redaction is thoughtfully implemented in configuration formatting, and cross-host requests discard authorization. That useful protection is separate from allowing an unauthenticated client to exercise the configured token's authority. The token transport compares host, not the full origin; same-host HTTP downgrade handling deserves a separate security test before relying on redirects from every possible configured upstream.

There is substantial test coverage: 88 Go files include 25 test files, with roughly 10,675 production Go lines. Fake Hub tests cover pagination, CDN redirects, ranges, errors, resumes, and normal corruption. CI runs multiple OS/Go combinations, race tests, and optional real-client checks. The missed failures repeatedly occur between otherwise tested components: timeout classification and fallback, manifest publication and GC, private metadata and policy, unverified streaming and client resume, or destination reuse and export guarantees.

The project also carries more scope than the NAS archive needs: torrent construction and seeding, upload caps, license classification, OMS signing, TUF registry publishing, and Ollama adaptation. These are optional execution paths, but they enlarge the review and maintenance surface. A deployment limited to the HF archive should not imply that all sharing and registry features have been qualified.

An independent read-only review corroborated the streaming, torrent traversal, retention, export, and registry integration findings. Its additional Ollama retention concern was subsequently reproduced here. Two proposed security findings are not counted as confirmed vulnerabilities: accepting previously cached TUF root metadata is normal root-rotation behavior under a trusted local-cache assumption, and spoofing forwarded headers changes the requesting client's response without demonstrating influence on another client. Shared-volume writers and trusted-proxy configuration still need explicit deployment boundaries. Hardlink exports can also share the archive's inode when selected or used as an automatic fallback; that is a material mutability tradeoff rather than proof of a remote integrity bypass.

## Operational and requirement gaps

- Imports remain an evaluation prototype, not an upstream CLI feature. Arbitrary partial directories missing regular sidecars require a policy or manifest change, as described in the followup evaluation.
- Cold verified serving waits for complete acquisition. Large-file timeout behavior and NAS throughput still need real-client qualification. Individual plain HTTP files over 50 GB are also blocked by SDK 2.0's client-side limit.
- There is no disk quota or space reservation before acquisition. Files are retained until explicit removal and GC. Disk-full errors therefore need deliberate testing, and maintenance must not use the currently unsafe concurrent GC paths.
- Proxy acquisitions run with `context.Background`; the HTTP download client has header/connection setup timeouts but no body-idle deadline. A body that stalls after headers can leave a flight and its partial lock occupied indefinitely. This is source-level evidence; an indefinite-duration stall was not benchmarked in this review.
- `CleanTmp` unlocks old lock files before unlinking them. Another process can acquire the old inode during that window, while a later process creates and locks a new inode at the same path. This is a source-level locking race, not a reproduced schedule in this review; a Go memory-race test does not detect filesystem lock-inode races.
- Serving uses a shared store and upstream identity, with no per-client access control. Cached gated content is explicitly exposed to every reachable client, and direct blob lookup is content based. The token passthrough defect adds upstream authority beyond this documented read exposure.
- Torrent operations use an embedded `anacrolix/torrent` client. `seed` is a long-running command; `pull --torrent` acquires and imports files. The HF serve process does not automatically use the swarm for a miss or manage an external qBittorrent/Transmission client.

## Validation and reproducibility

The clean upstream `go test ./... -count=1` run passed all packages except `internal/keep`, where `TestGC` failed with `Locks:0 Partials:1 Bytes:4`. Its fixed September 30 midnight clock no longer ages freshly created locks sufficiently. This is a test defect; the separately reproduced GC failures concern real retention logic.

`go vet ./...` passed. Race-detector tests passed for proxy, store, fetch, manifest, hfcache, and registry. A CGO-disabled Linux amd64 build of `cmd/weightkeep` succeeded, matching the NAS CPU and avoiding a runtime libc dependency. The binary was not run on Synology. Broad real-network compatibility, dependency vulnerability scanning, power-loss tests, and full NAS workloads were not repeated in this review.

The [reproduction patch](fixtures/weightkeep-code-review/reproductions.patch) adds fixture tests only; it does not change production code. Apply it to the pinned commit in a scratch checkout:

```sh
git clone https://github.com/afshinghezeli/weightkeep.git weightkeep-review
cd weightkeep-review
git checkout 933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3
git apply /absolute/path/to/reproductions.patch
go test ./internal/proxy ./internal/keep ./internal/hfcache ./internal/torrent -run '^TestReview' -count=1 -v
```

These tests assert the defective behavior and pass when reproducing it. A fix should invert the corresponding assertions into regression expectations. Streaming/client-resume reproductions and the verified-serving prototype are preserved separately in the [followup evaluation](hugging-face-cache-followup.md).

## Path forward

Retain WeightKeep as a candidate, with a larger hardening scope than the initial evaluation suggested. The core serving, offline timeout, manifest/GC, and export defects affect the intended archive and must be addressed before deployment. The token path must be restricted before a credential is configured. Torrent ingestion and private seeding require fixes before those commands are used; registry guarantees must be either implemented or removed from the advertised behavior.

After those correctness changes, repeat acquisition, restart, import, and recovery qualification with representative NAS models and the actual HF-based clients. This review supports a targeted hardening effort rather than deployment of the current release. It does not establish that the product is beyond repair, or that writing a new SDK wrapper would automatically avoid the same boundary failures.
