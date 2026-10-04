# Hugging Face cache followup evaluation

> **Status, October 4, 2026.** This is the September 30 evaluation. Its results and conclusions are kept as written below the line, and the fixtures are unchanged. One thing below the line was edited: the two Muninn commands under [Reproducing the qualification](#reproducing-the-qualification) originally named the scripts by bare filename, which did not resolve from the WeightKeep checkout the preceding block changes into, and now give them by absolute path. Read it with these corrections:
>
> - **Its recommendations are retired.** "Strongest candidate for a scoped extension", "choosing an implementation base" and the "bounded next qualification" were advice at the time. The [WeightKeep code review](weightkeep-code-review.md) widened the hardening scope the same day, the proposed qualification was not run, and no WeightKeep or Muninn work is planned. The current position is in the [main evaluation](hugging-face-nas-cache.md#current-position).
> - **Results describe pinned commits,** not what those projects are today.
> - **The qualification patch reproduces the modified results, not the stock defect.** It contains the production changes, and its corrupt-range and SDK-resume tests run with `VerifyBeforeServe: true` and assert the safe outcome. The stock corruption results below came from the same fixtures before that change. The tests-only [review reproductions](fixtures/weightkeep-code-review/reproductions.patch) do reproduce stock defects.
> - **The Pulsys fixture is now kept** as [`pulsys-nas-eval_test.go`](fixtures/hugging-face-cache-followup/pulsys-nas-eval_test.go), written for [`pulsys-io/pulsys`](https://github.com/pulsys-io/pulsys/tree/3699fc08ad5f94e92b838aa7cfbc9399b1e88cf0) at `3699fc08ad5f94e92b838aa7cfbc9399b1e88cf0` as `internal/proxy/zz_nas_eval_test.go`. Both tests call the proxy's CDN route directly with synthetic payloads and fail when they detect the defect. No run log was kept, and no real SDK client or NAS was involved. The retention test discards the first corrupt reply and fails only when the repeat returns 200 with the same bad bytes and no further upstream fetch. That detects a corrupt body replayed from a warm cache, which is the result reported here. It does not check cold or refetched bytes, so passing it would not show that the proxy verifies what it serves.
> - **The Muninn SDK 2.0 run was not pinned to a model commit.** The [script](fixtures/hugging-face-cache-followup/muninn-v2-eval.py) makes a metadata request and a download request, each against `main`. The cache kept from the run shows the download resolved to `71034c5d8bde858ff824298bdedc65515b97d2b9`; the commit the metadata request saw was not recorded. Because the metadata request's commit is unknown, the run cannot be repeated exactly. A stable repeat would pin both calls to the download's commit in an adapted copy of the script, which reproduces the download and may not reproduce the metadata the original run saw. No pinned copy was run.
> - **The Muninn offline script is a diagnostic, not an acceptance test.** The [script](fixtures/hugging-face-cache-followup/muninn-offline-eval.py) prints what it observed and makes no acceptance assertions: once its requests complete it exits 0 regardless of the response statuses, though a setup or network exception can still fail it. Exit 0 therefore means the script completed, not that anything was accepted, so the 502 responses are a defect it recorded, not a check that passed. It requests only the pinned resolve, repo-info and tree routes. The `main` ref it seeds and the refs route were never requested, so "refs also depend on upstream" is a source reading, not a run result. Its `cached_file_matches` field compares every response body with the config file's bytes, which means something only for the resolve route. For the two metadata routes the evidence is the 502 status; `false` there is not a schema check and would also be `false` for a correct metadata response.
> - **The 50 GB single-file limit is a source reading.** No file that large was needed or transferred.

---

Evaluated September 30, 2026. This followup tests newly identified options against the NAS archive requirements: original HF repository IDs through `HF_ENDPOINT`, selected weight downloads, cross-repository and cross-revision reuse, verified bytes, imports of existing files, and cached models usable after an upstream outage and restart. LM Studio was an example, not an active client or a selection requirement. The target remains Docker Compose on the x86_64 Synology NAS.

## Conclusion

WeightKeep is the strongest candidate for a scoped extension. Its existing global SHA-256 store, content-keyed download coordination, and durable repository manifests fit the requirements. An isolated prototype prevented unverified bytes reaching clients and imported existing files without model-file downloads. Those changes preserved cross-repository reuse and offline operation in the tested fixtures.

The subsequent [full code review](weightkeep-code-review.md) found additional security, availability, retention, and export defects. It materially expands the hardening scope beyond the two prototypes below. WeightKeep remains a useful storage foundation, but this followup's narrow qualification is not sufficient to recommend deploying the current implementation.

This supports choosing an implementation base, not replacing the NAS service yet. Stock WeightKeep has a demonstrated integrity failure involving HTTP resume. The prototype is not packaged, has no import CLI, and has not been exercised with large models on the NAS. Muninn remains relevant if native Xet ingestion becomes decisive, but an SDK upgrade alone is insufficient: verification, global acquisition coordination, and offline metadata need changes.

## Pinned candidates and scope

| Candidate | Evaluated source | Outcome |
| --- | --- | --- |
| WeightKeep | [`933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3`](https://github.com/afshinghezeli/weightkeep/tree/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3) | Best storage and serving foundation; extend verification and imports |
| Muninn | [`d11db64fac99b399720bda3d30ee1cac1af29a01`](https://github.com/skibare87/muninn/tree/d11db64fac99b399720bda3d30ee1cac1af29a01) | Broader modernization needed; SDK upgrade breaks verification |
| `huggingface_hub` | [v2.0.0](https://github.com/huggingface/huggingface_hub/tree/v2.0.0) | Useful acquisition library; does not supply a LAN HF server or all required storage coordination |

Pulsys was also tested during this search. Its request-addressed cache fetched identical content under different repository URLs twice and replayed a body whose hash differed from the advertised SHA-256. It does not meet the content reuse and integrity requirements as evaluated. ModelID adds an artifact-management abstraction rather than a transparent HF endpoint. These do not displace the two candidates above.

ModelKeep's commit archive design explains the original mismatch: repositories and revisions are the unit of retention, while content deduplication was deferred. The requirement here makes the content blob the appropriate unit of acquisition and storage. ModelKeep can be coherent as a snapshot archive while still being unsuitable for this workload.

## WeightKeep results

### Stock serving can return corrupt content

The server streams an in-progress download and withholds its final byte until verification. This protects neither complete early ranges nor resumed client files. In an injected-corruption test, a request for bytes 0–99 completed with HTTP 206 and 100 corrupt bytes before the server validated the file.

A separate test used real `huggingface_hub` 2.0.0 against a synthetic 26,738,688-byte weight. The first upstream response contained same-size bad bytes; the subsequent response was correct. The server rejected the first full-file hash, but the SDK resumed its local partial download and returned success with the wrong final SHA-256. The server's retained blob was correct. Server-side final verification therefore does not establish client-side integrity when earlier bytes have already escaped.

### Waiting for verification closes the tested failure

The prototype adds a `VerifyBeforeServe` server option. For a cold request it waits for the existing download flight to finish successfully, then serves the verified blob with the ordinary HTTP range implementation. It does not change acquisition or the store format. The option is enabled in qualification fixtures, not exposed by a production CLI flag.

| Test | Observed result with prototype |
| --- | --- |
| 12 concurrent clients, two repositories, renamed identical weight | One upstream weight GET; one blob record; all clients receive correct bytes |
| Unrequested quantization | Not downloaded or retained |
| New revision with unchanged weight | Zero additional weight GETs |
| Reopen store and server; upstream returns 503 | Both commit pins and expired `main` serve cached bytes |
| Corrupt early range | Request fails; no successful corrupt range and no corrupt retained blob |
| Real SDK corruption and retry | First attempt rejected; fresh retry returns the expected SHA-256 |
| Cold client timeout | Background ingest survives cancellation; next client receives correct bytes from the same single upstream fetch |
| Real SDK, 12-second cold stall, 10-second timeout | SDK logs a timeout and automatically retries; correct final hash; one upstream weight GET |

The cost is time to first byte: a cold client waits for the entire upstream fetch and verification. SDK 2.0 defaults to a 10-second download timeout and retries transient timeout failures. A real SDK test stalled the upstream body for 12 seconds: it logged the timeout, retried automatically, and returned the correct hash using the same NAS fetch. Large files can still outlast retries or reverse-proxy timeouts. These tests demonstrate recovery, not acceptable large-file latency. Prewarming or deliberate timeout configuration must be qualified with actual clients and NAS throughput.

A separate stock-server smoke test used real SDK 2.0.0 with `prajjwal1/bert-tiny` pinned to `6f75de8b60a9f8a2fdf7b69cbd86d9e64bcb3837`. It downloaded the 17,756,393-byte weight with the expected hash, then fresh client caches loaded the pin and `main` after restarting the server in offline mode. Client HTTP was restricted to the local endpoint and client Xet download bypass was prohibited. This establishes a small real-model compatibility case, not a full client matrix or large-model benchmark.

### Importing existing files is feasible with bounded changes

The importer prototype obtains repository metadata, builds the existing manifest, and passes local bytes through the existing verified store writer. It accepts native HF snapshot symlinks and ordinary directories. An explicit source-path mapping supports renamed files. LFS files are checked against upstream SHA-256; regular Git files are checked against their Git blob IDs. Source files remain available.

Fixture results:

- Imported config, license, and one selected weight through native HF snapshot symlinks without any model-file GETs. Missing unselected weights remained on demand.
- Imported a second repository whose weight had a different upstream and local filename: all three existing content blobs were reused, with no new weight blob or payload download.
- Rejected a same-size corrupt source and did not publish its revision manifest.

There is an important partial-cache limit. Manifest v1 requires a SHA-256 for every regular file, so this prototype cannot publish a revision if uncached regular sidecars are missing. A complete native cache or matching plain directory works; an arbitrary directory containing only a weight needs additional handling. An implementation must either acquire missing small sidecars explicitly or change the manifest's representation of not-yet-acquired regular files. WeightKeep itself currently preloads all non-LFS files when learning a revision.

Imports also need trustworthy repository/revision metadata. The tests fetch metadata from the upstream but make no model-file downloads; they do not establish fully offline import of unidentified model folders. A cache scanner, provenance mapping, import progress, and CLI error behavior are not implemented.

### Validation limits

The prototype package run passed `internal/proxy`, `internal/fetch`, `internal/store`, `internal/manifest`, `internal/hfcache`, and `internal/keep`, skipping only `TestGC`. The stock `TestGC` failed because its fixed September 30 midnight clock no longer made freshly created lock files old enough for its assertion. This is not a clean full-suite result, nor evidence that operational GC is correct. Torrent and seed subsystems were not part of this qualification.

No test established large-file latency, NAS disk-full handling, abrupt process death, or production gated/private access control. WeightKeep can use an upstream token, but its serving endpoint does not authorize individual LAN clients. Gated content therefore needs an explicit access boundary before it is cached for multiple users.

## Why Muninn needs more work

Muninn pins `huggingface_hub[hf_xet]` to 0.34.4. Version 2.0.0 introduces a shared Xet blob store, but Muninn's verifier derives the expected SHA-256 from the resolved file's basename. That basename is now the Xet identifier rather than the LFS SHA-256.

A version-bump experiment downloaded the real 3,561,811-byte `hf-internal-testing/tiny-random-gpt2/pytorch_model.bin`. Its bytes matched upstream SHA-256 `4fab47c129967e0db58e8faf8494e4bd04f2ea79bbe287ac2f90c4183c0194be`; its resolved shared-store basename was Xet identifier `72d5008077bcb694b439abace86b6708478cbba54d128303d755a02ea505fa0a`. Muninn raised `IngestDigestMismatch` and deleted the good blob. This is a targeted compatibility experiment, not a fully upgraded Muninn deployment.

An independent default local-cache test seeded a valid pinned snapshot and made the upstream unreachable. The cached file returned HTTP 200 with correct content. Repo-info and tree requests for the same commit returned 502. Source inspection shows their local metadata fallback is for upstream 404, not connection failure or 5xx; refs also depend on upstream. Fresh SDK clients need this metadata even when the file bytes are already present. The test uses the ASGI handler without background lifecycle tasks and does not cover the optional external object-storage tier.

Further source-level gaps remain: job keys include repository and filename, SDK 2.0's acquisition locks remain repository scoped, default streaming exposes partial downloads before verification, and capacity-based eviction is active unless retention is configured. A shared retained blob store does not itself guarantee one WAN acquisition across concurrent repositories. Fixing these requires changes beyond substituting the SDK version.

## What using the SDK directly buys

`huggingface_hub` is directly usable for download, cache lookup, metadata, and file selection. Version 2.0's shared Xet store improves retained-file reuse across repositories. It is a useful foundation if native Xet acquisition is required.

A NAS service built around it still needs durable repository metadata and refs, offline HF API responses, global content-keyed acquisition coordination, verified publication, import provenance, and retention policy. That is substantial server behavior even if the acquisition call is small. WeightKeep already implements much of it.

WeightKeep serves plain HTTP and removes Xet download signals. SDK 2.0's [HTTP download implementation](https://github.com/huggingface/huggingface_hub/blob/v2.0.0/src/huggingface_hub/file_download.py) refuses an individual plain HTTP download larger than the [50,000,000,000-byte limit](https://github.com/huggingface/huggingface_hub/blob/v2.0.0/src/huggingface_hub/constants.py). A model made of smaller shards does not hit that individual-file limit. A workload needing larger single files requires an explicit client or serving solution; the current prototype does not address it.

## Reproducing the qualification

The [qualification patch](fixtures/hugging-face-cache-followup/weightkeep-qualification.patch) contains the serving option, importer prototype, and fixture tests. Apply it to the pinned WeightKeep commit in an isolated checkout. This is evaluation code, not an upstream-ready change. Go, `uv`, and `rtk` must be installed; the SDK test uses `uv` to resolve version 2.0.0 and requires dependency-download access.

```sh
git clone https://github.com/afshinghezeli/weightkeep.git weightkeep-eval
cd weightkeep-eval
git checkout 933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3
git apply /absolute/path/to/weightkeep-qualification.patch
go test ./internal/proxy ./internal/fetch ./internal/store ./internal/manifest ./internal/hfcache ./internal/keep -skip '^TestGC$' -count=1
```

The two Muninn scripts take the checkout directory as their first argument and create isolated temporary caches. Clone Muninn and check out the pinned commit before running them. The offline script uses the original dependencies and injects a connection failure. The SDK 2.0 script contacts Hugging Face for a small public weight, prints the valid hash, then exits nonzero when Muninn's verifier rejects it; that failure is the reproduced finding.

```sh
uv run --no-project --with-requirements /path/to/muninn/requirements.txt python /absolute/path/to/home-lab/docs/evaluations/fixtures/hugging-face-cache-followup/muninn-offline-eval.py /path/to/muninn
uv run --no-project --with huggingface-hub==2.0.0 --with fastapi==0.115.6 --with httpx==0.28.1 --with uvicorn==0.34.0 --with bcrypt==5.0.0 --with 'PyJWT[crypto]==2.10.1' python /absolute/path/to/home-lab/docs/evaluations/fixtures/hugging-face-cache-followup/muninn-v2-eval.py /path/to/muninn
```

The bounded next qualification is to exercise verified cold serving and the import path with a representative large NAS model and the actual HF-based clients, then choose the partial-sidecar import behavior. Those results determine whether WeightKeep's remaining work stays small. This report records the evaluation; it does not establish a deployment decision.
