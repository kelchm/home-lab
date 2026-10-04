# Hugging Face downloads retained on Athena

Evaluated September 26–28, 2026, and reassessed September 30–October 4. The September work compared mirror servers with source review, isolated macOS probes and disposable pull-through instances on Athena, and selected ModelKeep v0.4.12 as a public-models pull-through mirror. **That selection no longer stands as the answer:** ModelKeep keeps running, but it does not reuse content across commits or repositories, which the September tests never checked. Nothing replaces it, and no further evaluation or build is planned.

Related records:

- [Broader landscape](hugging-face-cache-landscape.md): other mirror servers, download managers and distribution systems, and the candidates found later.
- [Followup evaluation](hugging-face-cache-followup.md), September 30: WeightKeep, Muninn, Pulsys and ModelID against the fuller contract.
- [WeightKeep code review](weightkeep-code-review.md), September 30.
- [hf-archive experiment](hugging-face-sdk-archive-experiment.md), October 3–4: an SDK-based prototype, retired, and what it showed about stock client behavior.
- Structured evidence for the September probes: [Shpiel](hugging-face-nas-cache-evidence.json), [MatrixHub behavior](matrixhub-local-evidence.json), [MatrixHub small import](matrixhub-import-evidence.json), [actual Mac imports](matrixhub-mac-import-evidence.json) and [MatrixHub acceptance gates](matrixhub-gate-evidence.json). The [Athena pull-through evaluation](#athena-pull-through-evaluation) records the NAS measurements.

[Investigation #608](https://github.com/kelchm/home-lab/issues/608) recorded the September 28 decision and [#634](https://github.com/kelchm/home-lab/issues/634) tracks the running ModelKeep service.

## Current position

As of October 4, 2026:

- **No evaluated option meets the whole [archive contract](#the-archive-contract-and-what-september-did-not-test), and none is selected as the answer to it.** Each candidate was measured at a pinned version and passed some gates and failed or skipped others. That is not a finding that these projects cannot meet the contract: later releases were not re-tested, and several candidates were only read, not run.
- **ModelKeep v0.4.12 keeps running unchanged on Athena as a pull-through mirror for public repositories.** Its September 28 results stand for what they tested: selected-file fetching, offline serving after a restart, one shared upstream transfer for concurrent clients of the same ref, and hashes matching Hugging Face. It is not a complete answer because it stores every revision as its own set of files: when a client requests files at a new commit, it downloads those files again even when identical content is already held under another commit or repository. It still fetches only the files requested.
- **Nothing is being built or qualified in its place.** The WeightKeep and Muninn followup was not pursued past September 30. The hf-archive prototype was retired on October 4 as a learning exercise: [PR #711](https://github.com/kelchm/home-lab/pull/711) is not merged and [#710](https://github.com/kelchm/home-lab/issues/710) is closed as not planned.
- **Earlier next steps are retired.** The MatrixHub hosted-library qualification ([#614](https://github.com/kelchm/home-lab/issues/614), closed as not planned), the WeightKeep large-model qualification and the hf-archive whole-library qualification will not run. Where a section below still describes one as upcoming, it is recording what was planned on its date.

| Candidate | Version examined | How | Disposition |
|---|---|---|---|
| ModelKeep | v0.4.12 | Run on Athena; code review | Running as a public pull-through mirror. No reuse across commits or repositories, so not the archive |
| HugRS | v0.7.1 | Run on Athena; code review | Rejected: fails offline and corrupted files for clients joining a download |
| MatrixHub | v0.2.0 | Run on macOS | Not used. Hosted mode serves its own names instead of original repository IDs; proxy mode prefetched unrequested files, failed offline after metadata expiry and kept injected corruption. It does store large files once by hash |
| Shpiel | v0.3.1 | Run on macOS | Baseline only: `/refs` returned 404 and cold files are buffered whole. Not checked for cross-repository reuse |
| WeightKeep | `933b3c9` | Fixtures and code review on a workstation | Not pursued. Reused content across repositories and commits, but the review confirmed nine areas of defects, including stock serving that delivered corrupt bytes to a resuming client |
| Muninn | `d11db64` | Two targeted scripts | Not pursued. Moving it to SDK 2.0.0 made its verifier reject a valid weight; cached metadata answered 502 with upstream unreachable |
| Pulsys | `3699fc0` | Two synthetic proxy-route tests | Not pursued. Fetched identical content twice under two paths and served a body that did not match its advertised hash |
| hf-archive | `9c119dc` | Prototype run on Athena and a workstation | Retired, not merged. Never qualified beyond one 4.1 GB shard and a 17 MB model |
| Olah, Nexus CE, Pulp, xet-server, others | Various | Documentation and source only | Not run. See the [comparison](#candidate-comparison) and the [landscape](hugging-face-cache-landscape.md) |

A retained filesystem library on Athena remains the foundation: public retention and private training-output preservation must not depend on an OCI registry, S3 service or paid product. Preserve existing Spark and Vonk cache contents; nothing evaluated here is qualified as their sole copy.

## The archive contract and what September did not test

The goal throughout was to download each file from the internet once and keep it. The September acceptance tests were narrower than that goal. They defined "download once" as a second empty client obtaining an already archived revision with no internet transfer. They never requested a second commit of a repository the mirror already held, and never requested the same file under two repository names.

On September 30 that gap surfaced while existing caches were being imported:

- **ModelKeep does not share content between revisions, by design.** It materializes each revision as ordinary files under its commit, and its ADR-0001 states that the archive "may duplicate identical blobs across revisions" and that deduplication was intentionally left out. This is a reading of its design and source, at `docs/adr/0001-ordinary-files-as-durable-archive.md` in v0.4.12 (`535dbbeab9acbfcecb93f5be4fea092c3b29d7db`). No second-commit download was run to measure it. Source availability, October 4: GitHub answered 404 for the `kaznak/modelkeep` repository and for that pinned file, and why, or whether the project moved, was not established. The quotation, the path and pin, and the probe fragment below therefore rest on the reviewer's log preserved from September, and the ModelKeep source links in these documents are historical references.
- **The cost is large for the models held here.** The two commits of `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` in use, `25a44fd` pinned by SparkRun and `9eaebb7` at `main`, share 123 of 144 files, 175.7 GB, byte for byte. That figure comes from the inventory taken during the import and was reported in the working session; no inventory file was kept.
- **Filesystem deduplication recovers the disk, not the transfer.** Btrfs block sharing on Athena can collapse identical files after they arrive. It cannot avoid downloading them again.
- **One file addressed two ways is also fetched twice.** In a unit probe against ModelKeep v0.4.12 (`535dbbeab9acbfcecb93f5be4fea092c3b29d7db`) with a synthetic upstream fetcher, requests for one file by `main` and by its commit SHA, issued from two threads, were reported on September 29 to start two upstream transfers. The [probe fragment](fixtures/modelkeep-review/mixed-alias-probe-recovered.rs) was recovered from the reviewer's log: it prints the transfer count without asserting it, is not a standalone test, and was not re-run. It also does not force the two requests to overlap. The second thread is launched 50 ms after the first, and the synthetic fetch sleeps 300 ms, but nothing confirms the first fetch had begun, so actual overlap is not guaranteed: the reported count shows two fetches for one file but does not prove they ran at the same time. That ModelKeep stores each revision's files separately is a source reading and does not rest on this probe. The live concurrency test on Athena used `main` for every client and passed within that scope; the mixed case was not run live.

That made ModelKeep unacceptable as the archive. The contract the later work was judged against:

1. Clients use original repository IDs, revisions and paths through `HF_ENDPOINT`, over the network, with no per-model registration.
2. Only requested files are acquired. No background fetching of other quantizations, formats or versions.
3. Each unique file is stored once and never downloaded again when it is already held, across commits, repositories and filenames, including for concurrent requests.
4. Bytes are verified against upstream identity before any of them reach a client.
5. Held files and the metadata clients need are served to an empty client after a restart with upstream unreachable.
6. Existing files can be imported without downloading them again, keeping their original repository and commit identity. That covers both Hugging Face cache layouts and snapshots that are partial on purpose, such as one quantization.
7. Public repositories only, until there is a separate authorization boundary.

The inventory behind item 6, as reported on September 30 and not kept as a file: 676 GB on the Vonk share matched Hugging Face files across six repositories; the Mac's Hugging Face cache held 197 GB across 13 revisions, three of them complete and the rest partial by design; the Sparks hold pinned commits that overlap the Vonk content. ModelKeep's importer rejected the Mac cache's newer shared-blob layout, so those files were copied to Athena with links resolved but never imported. LM Studio was an example consumer during this work, not an acceptance client, and its folders record no commit.

### State of the ModelKeep service

ModelKeep was deployed from [PR #635](https://github.com/kelchm/home-lab/pull/635) on September 29. By September 30 it held six imported revisions, about 630 GiB, checked against Hugging Face's hashes during import; an import of the Sparks' pinned commits was stopped before it published anything. Those figures are from the working session on that date and were not re-measured. On October 4 the container was observed healthy with its start time unchanged since September 29. The deployment is a working mirror for what it holds, not an accepted archive, and holding those revisions does not mean any application was tested end to end against them.

## September 28 recommendation

Kept as the record of what was decided on that date; see [Current position](#current-position) for what holds now.

**Run ModelKeep v0.4.12 on Athena as a pull-through mirror for public repositories.** The requirement is a transparent proxy: any LAN client sets `HF_ENDPOINT`, requests original repository IDs, and each file crosses the internet once. In the [Athena evaluation](#athena-pull-through-evaluation), ModelKeep fetched only requested files, served everything archived with Hugging Face unreachable and after a restart, shared one upstream transfer among concurrent clients, and matched Hugging Face's hashes throughout. It stores ordinary files that stay readable without the service, which keeps the retained-library principle.

Its known limits are operational: a large file that is not archived yet returns `503` after 8 seconds, so archive large models ahead of use; it compares nothing with Hugging Face's published digests, so an external hash check runs against its records; and a code review found bounded defects to report upstream. The deployment serves public repositories only, without a management listener or Hugging Face token.

**HugRS v0.7.1 is rejected.** It streamed cold files well, but every cached model failed with Hugging Face unreachable, and clients joining an in-progress download received the file shifted by one 4 MiB chunk; one client finished with exit 0 and wrong content. Both causes are architectural. **MatrixHub v0.2.0 is not used for this role.** Its hosted-library mode serves MatrixHub names rather than original repository IDs, and its proxy mode retains the fetching, offline and verification failures recorded below. That supersedes the September 27 recommendation to continue MatrixHub as a curated hosted library. Shpiel remains the first measured baseline; its `/refs` 404 breaks llama.cpp and it buffers whole cold files before serving.

Preserve existing Spark and Vonk cache contents until a hash-verified LAN import succeeds. If an endpoint cannot reuse them, stage those models by local path rather than downloading the collection again for a new storage format.

## Local environment

The September 26 read-only SSH inspection of Athena returned:

| Property | Observed value |
|---|---|
| Architecture | `x86_64` |
| RAM | 32,071 MiB total; 29,894 MiB available |
| `/volume1` | `df -h`: 49T total, 20T used, 29T available |
| Docker Engine | 24.0.2 |
| Docker Compose | 2.20.1-6047-g6817716 |

This establishes capacity for a trial, not throughput or a service memory budget. The [architecture reference](../architecture.md) identifies the DS1821+, disk pool and storage network. The [NAS workload model](../../synology/README.md) is explicitly applied Compose; [#379](https://github.com/kelchm/home-lab/issues/379) tracks the shared deployment plane. Run the service and bind-mount its storage directly on Athena.

[SparkRun](../../sparks/inference/sparkrun/README.md) uses `/opt/spark-cache/huggingface` and synchronizes to its worker. Inspection of the primary Spark's host download environment found SparkRun 0.3.9, `huggingface_hub` 1.8.0 and `hf-xet` 1.6.0. Recipe environment settings do not prove that the earlier host-side downloader uses a mirror. [PR #529](https://github.com/kelchm/home-lab/pull/529) describes existing Vonk/NAS data to preserve, not a general HF endpoint.

## Candidate comparison

This is the initial comparison, from September 26, with dispositions corrected on October 4 where later work changed them. The [expanded landscape](hugging-face-cache-landscape.md) adds direct mirror servers, NAS download managers and model-distribution systems, with project history and concrete reasons to pursue or deprioritize them. Project age, transparent mirroring and durable retention are separate properties.

| Candidate | Evidence and fit | Disposition |
|---|---|---|
| **[Shpiel](https://github.com/loewenthal-corp/shpiel)** | Apache-2.0; one Go process, filesystem backend, no database required. Repository created July 2026. | Measured public-only baseline; reuse and gaps below. Not checked for reuse across repositories or commits. |
| **[MatrixHub](https://github.com/matrixhub-ai/matrixhub/releases/tag/v0.2.0)** | Apache-2.0; September 18 release added SQLite for single-node Compose. Proxy projects and hosted models with permissions. | Not used. Real 8.1 GB hosted imports, reuse and offline restart passed, but hosted mode does not serve original repository IDs, and the transparent proxy has refresh, selection and verification failures. [#614](https://github.com/kelchm/home-lab/issues/614) is closed as not planned. |
| **[Nexus CE](https://help.sonatype.com/en/hugging-face-repositories.html)** | Documented models/datasets proxy with local permissions and upstream bearer credentials. Broader repository manager. | Conventional fallback; HTTP-only, buffering, quotas and import cost matter. |
| **[Olah](https://github.com/vtuber-plan/olah)** | MIT; block cache and offline mode. README instructs deleting incompatible caches across upgrades; [issue #85](https://github.com/vtuber-plan/olah/issues/85) reports a missing llama.cpp refs endpoint. | Not run. The upgrade instruction is a retention concern. The refs report is from 2025 and was not reproduced; source at [`c70b2f8`](https://github.com/vtuber-plan/olah/tree/c70b2f846dd205c9a23e805cda593ec69cefe4f6) (0.5.2), read on October 3, has refs routes and offline tests, so it is not a known current defect. |
| **[guilt/xet-server](https://github.com/guilt/xet-server)** | MIT; native Xet proxy/server. Created September 11; inspected release v1.1.0. Its [mirroring guide](https://github.com/guilt/xet-server/blob/22c4aa1df566041b804aee2d4b1df1b97e2f04df/docs/MIRRORING.md#91-important-what-the-proxy-can-and-cannot-cache) says real-Hub pull-through can relay uncached content directly from the CDN. | Transparent proxy does not guarantee retention. Complete offline seeding requires download then upload. |
| **[Pulp HF plugin](https://github.com/pulp/pulp_hugging_face)** | GPL-2.0-or-later; on-demand files within Pulp. Metadata API requests are forwarded live upstream. | Additional platform and unresolved offline behavior here. |
| **[HuggingHack](https://github.com/tyedalwaves/HuggingHack)** | MIT; NAS file library, download UI, existing-folder indexing and private uploads. | Relevant path-based UI; not documented as an HF_ENDPOINT proxy. |
| **[KohakuHub](https://github.com/KohakuBlueleaf/KohakuHub)** | AGPL-3.0 full Hub; LakeFS/S3/database stack, external-source fallback. | More infrastructure than the initial cache needs; fallback is not retention evidence. |
| **[Shardline](https://github.com/STEXS-Technologies/shardline)** | MIT/Apache-2.0 multiprotocol store; filesystem/SQLite options. [Hub API is beta](https://github.com/STEXS-Technologies/shardline/blob/main/docs/COMPATIBILITY_STATUS.md). | Future artifact-backend candidate; Hub compatibility does not establish upstream pull-through. |
| **[Artifactory](https://jfrog.com/blog/native-xet-support-in-jfrog-artifactory/)** | Native Xet and hosted/remote HF. [Self-managed feature matrix](https://docs.jfrog.com/installation/docs/feature-comparison-matrix-for-self-mangaged-jpds) excludes HF from free editions. | Outside the preferred community-software scope for the initial cache. |
| **[Native HF cache](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache)** | Official client, retained files, explicit revision prefetch/staging. No Hub server required. | Architectural fallback when transparent on-demand HTTP is not essential. |

## Shpiel: measured behavior

The checksum-verified Darwin ARM64 [v0.3.1 release](https://github.com/loewenthal-corp/shpiel/releases/tag/v0.3.1), commit `18156f1299234f084ba623c910351d1dbf6c25ba`, ran on loopback with scratch storage. An instrumented gateway relayed public HF traffic, then returned errors to simulate unavailable upstream. No real HF credentials or existing user caches were used. Isolated clients were `huggingface_hub` 2.0.0 and 1.8.0, both with `hf-xet` 1.6.0.

The fixture was `sshleifer/tiny-gpt2` at `5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be`: `config.json` (662 bytes) and `pytorch_model.bin` (2,514,146 bytes). This tests protocols, not a complete usable snapshot or NAS performance.

| Probe | Observed result |
|---|---|
| Cold HF 2.0 client, then second empty client | Cold payload fetched once; repeat returned identical SHA-256 hashes and made zero upstream gateway requests. |
| Restart, unavailable upstream, new empty client | Pinned revision succeeded with matching hashes and zero upstream requests. A `main` repeat worked within the metadata refresh interval; expiry was not tested. |
| Four simultaneous cold HF 1.8 clients | Matching files; each payload fetched once. Metadata fetched three times, so this is not zero-request deduplication. |
| Warm byte-range read | HTTP 206, exactly bytes 100–199. Cold-range and interrupted-resume behavior not tested. |
| Classic HF cache import without Shpiel sidecars | Offline lookup failed. One upstream metadata request made both existing blobs usable without another payload download. New shared-blob layouts not tested. |
| Direct native-client filesystem read | HF 2.0 offline/local-only reads returned both retained files with matching hashes while the service was stopped. |
| Native Xet upload/download of synthetic local file | HF 2.0 transferred 2 MiB with matching hash. Logs confirm xorb/shard/reconstruction traffic and fallback from unsupported v2 routes to v1. A separate buffer upload used LFS/HTTP. |
| `GET /api/models/sshleifer/tiny-gpt2/refs` | HTTP 404. Do not claim native llama.cpp downloader compatibility. |
| Synthetic repo created with `private=True` | Anonymous cached-file GET succeeded after restarting with `auth.mode: passthrough`. This setting is not a private-artifact access boundary. |

Public upstream weights used ordinary HTTP even with Xet enabled: HEAD did not advertise reconstruction metadata. [The handler](https://github.com/loewenthal-corp/shpiel/blob/v0.3.1/internal/server/handlers.go) advertises Xet when it holds local reconstruction records. Native Xet for uploaded content is distinct from upstream Xet mirroring. Public probes did not require disabling Xet globally.

[Relay source](https://github.com/loewenthal-corp/shpiel/blob/v0.3.1/internal/relay/relay.go) caches a cold file completely before serving it. Prewarming and a measured client timeout are necessary for large files. [Filesystem source](https://github.com/loewenthal-corp/shpiel/blob/v0.3.1/internal/backend/fsbackend/fsbackend.go) verifies newly fetched content but trusts existing blobs, keeps per-repository objects and needs sidecar manifests. Atomic rename does not establish power-loss durability. Verify imported hashes and preserve sidecars; the small probes do not establish cross-repository deduplication or crash recovery.

## MatrixHub: local trial and source findings

The September 26 local trial built release tag v0.2.0, commit `b39cfcbd5bb1f29f6d9a0722dd6bee3b9835d6ea`, with Go 1.27.0, SQLite and the built web UI on macOS ARM64. Docker was not running on the Mac. Two address-only source changes confined HTTP/gRPC listening and internal dialing to loopback; SSH was disabled. No cache or authorization logic was changed. [Structured evidence](matrixhub-local-evidence.json) includes the exact patch, hashes and request records.

A disposable public registry/project pointed through an instrumented gateway to HF. The gateway counted Git/LFS/CDN response bodies and later injected HTTP 503; it buffers bodies, so timings are not performance evidence. Each official HF client probe used an empty cache without real HF tokens. The fixture was the complete nine-file, 4,734,064-byte `sshleifer/tiny-gpt2` snapshot at `5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be`.

| Probe | Observed result |
|---|---|
| Complete snapshot with HF 1.8.0; repeat from another empty cache | Both succeeded with identical hashes. The repeat made zero upstream requests. |
| `/refs` and warm byte range | Refs returned `main` at the pinned commit. Range 100–199 returned HTTP 206 and exactly 100 bytes. |
| Restart with upstream unavailable, before metadata expiry | Complete snapshots succeeded with HF 1.8.0 and 2.0.0; both pinned and `main` were exercised. Zero upstream requests. |
| Upstream unavailable after actual metadata expiry, without changing the clock or database | At about 70 seconds after the cold start, pinned range reads, refs and fresh-client snapshots failed. HF 1.8.0 failed for pinned and `main`; HF 2.0.0 failed for pinned. Each attempted upstream Git refs and received local HTTP 500. |
| Restore upstream | Complete snapshots recovered with matching hashes. HF 1.8.0 needed two Git metadata requests totaling 556 response bytes and no weight payload; the following HF 2.0.0 download needed no upstream requests. |
| Temporarily mark the cached public fixture project private | Anonymous, invalid-token and revoked-token reads returned 403. A valid local admin bearer token read the 2,514,146-byte cached weight with its expected hash. Browser session cookies alone returned 403 on the HF route. Project visibility was restored and the disposable token deleted. |
| Browser UI | Login, project/model browsing, nine-file listing and configuration viewing worked. The frontend required login even for the public fixture, while public HF API reads worked anonymously. Download/use dialogs supplied endpoint-aware `hf download` and `vllm serve` commands; inference itself was not tested. |

The offline failure is reproduced, not just inferred from source. The files remain on disk and recovery does not redownload weights, but an empty LAN client cannot obtain them through this endpoint during the expired-metadata outage. WAN payload reuse and offline availability are separate results. The subsequent acceptance gates exposed selection and integrity gaps; the later real-file imports below establish a useful hosted workflow despite those proxy limitations. No cache or authorization fix was applied during the trial. The narrow private-project smoke check is encouraging; it does not qualify real gated/private upstream ingestion, unrelated-user isolation, alternate object routes or fine-tune hosting.

The v0.2.0 [Compose deployment](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/deploy/docker-compose.yml) uses one service. [SQLite configuration](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/deploy/config.yaml) keeps the database in its data directory. Its [guide](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/docs/development.md) excludes SQLite on NFS/SMB or shared by multiple processes; direct NAS bind mounts fit.

Proxy setup is per organization/project: the [guide](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/website/docs/guides/mirror-from-huggingface.md) creates a `Qwen` proxy project before requests to `Qwen/...`. The trial used `sshleifer` this way. [Read handlers](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/apiserver/handler/hf/handler_hf_download.go) check repository permission and can stream in-progress weight downloads; large-file streaming was not qualified.

- **Offline after refresh expiry, reproduced:** [synchronization](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/domain/model/model_service.go) propagates remote-pull failures before opening a proxy repo, with a [one-minute TTL](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/domain/model/model.go). The live results above match this failure path. Any fix needs the same expired-metadata test; an immediate warm repeat is insufficient.
- **Integrity and unwanted prefetch, reproduced below:** the pinned [hfd cache](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/mirror/tee_cache.go) queues background objects beyond an immediate file request. Its local persistence path checks length then calls [MovePut](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/lfs/local_storage.go), which renames without hashing. Both the unwanted acquisition and persistent corruption now have live reproductions. [Issue #598](https://github.com/matrixhub-ai/matrixhub/issues/598) also requests complete-model verification.

The [roadmap](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/ROADMAP.md) places several README-advertised features in later phases, including Xet downloads, S3 and scanning. Qualify the pinned release instead of treating its feature list as shipped behavior.

### MatrixHub acceptance outcome

**Transparent-proxy limitations remain; the later hosted-import results narrow the operating-mode decision.** The initial blanket no-go was reconsidered against selected, prewarmed, externally verified model use. Two additional fresh instances used the same release binary and loopback-only patch, separate SQLite/Git/LFS stores and empty official-client caches. The public fixture remained the pinned tiny-gpt2 snapshot. [Gate evidence](matrixhub-gate-evidence.json) contains the fault definition, reproduction steps, traffic and hashes. The new runs transferred 7,875,650 gateway response bytes, including 6,755,816 weight bytes, well below the 8 GiB ceiling. These are protocol/fault probes, not performance measurements.

| Gate | Result and consequence |
|---|---|
| Selected-file acquisition | **Fail.** HF 1.8.0 requested only the 662-byte `config.json`. Within the five-second observation window, the server fetched and persisted all three unrequested weights: PyTorch, TensorFlow and Flax, totaling 3,377,908 bytes. Selecting a file at the client does not bound server acquisition. This fixture proves alternate-format prefetch. The later actual Orpheus import also reproduced attempted prefetch of other GGUF variants; historical-version acquisition was not separately tested. |
| Same-length corruption rejection | **Fail.** The gateway verified the correct 2,514,146-byte PyTorch response, then flipped one bit at byte offset 1024 without changing length. HF 1.8.0 reported successful download. The persisted object and downloaded file had SHA-256 `9fa26e3e067c00a629f71bc75abe6af326dbecafb521600cbe8338d96dd516b1`, instead of the expected `b706b24034032bdfe765ded5ab6403d201d295a995b790cb24c74becca5c04e6`. This was deliberate fault injection, not an assertion that HF supplied a damaged file. |
| Corruption after restart | **Fail.** Restarting the backend, disabling injection and downloading into a new HF 2.0.0 cache returned the same damaged bytes successfully with zero upstream requests. HEAD returned 200 and advertised the original expected hash as ETag. Warm reuse therefore perpetuated the corruption. |
| Offline after metadata expiry | **Fail in the earlier baseline.** No runtime fix was implemented after the additional failed gates. |
| Normal tiny snapshots, repeated use and existing-weight seeding | **Pass for the recorded fixtures.** Seven complete baseline snapshots matched hashes; the hosted-seed/proxy import preserved the original proxy revision with no upstream weight transfer. |
| Private hosting | **Unqualified.** The earlier admin-token smoke check passed, but unrelated users, direct LFS access, gated upstreams and restore were not qualified. |

An offline-only patch would not address unbounded acquisition or verification for unrestricted proxy use. Curated hosted imports avoid remote refresh and unwanted variants, and can be independently verified before use. The [pointer scan](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/repository/lfs.go) iterates repository blobs and the [mirror](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/mirror/mirror.go) queues their LFS objects; selection would need a server-side acquisition policy. MatrixHub's [metadata reader](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/repo/git_repo.go) can also promote safetensors downloads while inspecting headers, so disabling only the idle prefetch worker is not enough to establish selected-file behavior. Integrity needs verification before an object is accepted as complete, error propagation to readers and tests for in-progress streaming; adding a hash check after sending all bytes would not establish that contract. Offline serving separately needs local-revision availability, refresh and authorization regression checks. These are repair directions, not tested fixes or an estimate that repair is impossible.

MatrixHub main at `05d158d5eb079b263b45215984d111d525cf81a0` still [pins the same hfd dependency](https://github.com/matrixhub-ai/matrixhub/blob/05d158d5eb079b263b45215984d111d525cf81a0/go.mod) and propagates the remote-pull failure in `CheckOrSyncFromRemote`; main was inspected, not executed. Newer standalone hfd has [verified ingestion code](https://github.com/matrixhub-ai/hfd/blob/d2ba5f3640aa0df3bb8830565d9bdc232524bcec/pkg/mirror/ingest.go), but changes the storage/API to Xet and still has background prefetch. It is not evidence that the tested MatrixHub release is fixed, nor a qualified dependency upgrade.

The initial gate run stopped before larger imports. The follow-up below tests actual Mac downloads with hash verification and an upstream weight-download block; no fault result is erased or reported as repaired. Four-client cold concurrency, full collection inventory, interrupted/resumable imports, staging cleanup, export/backup restoration, quota exhaustion, truncated transfers, native llama.cpp, actual inference and broader private access checks remain **untested**, not passed. The read-only head-Spark inventory only counted two model repos and three snapshot directories with no broken links; file counts alone do not prove completeness. No worker/NAS inventory or migration was performed.

Retain original Spark/NAS files; MatrixHub's internal store was never qualified as the sole archive. The two fault-injection services were stopped and their isolated evidence retained. On September 27, all local evaluation services and gateways were stopped, and their model stores, imported copies, disposable client caches, binaries, build trees and virtual environments were removed at the user’s request. Original model downloads and lightweight research records were preserved; future runtime tests require recreating the isolated trial. The next check planned on September 27 was service-independent export/restore and retained-byte lifecycle for hosted models. It was not run: original upstream identities became the client contract the next day, which hosted mode does not provide, and #614 was closed as not planned.

### Loading existing local files

MatrixHub's [upload guide](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/website/docs/operations/model-repo/upload-download.md) documents `hf upload` into a hosted project and explicitly excludes uploads to proxy projects. A plain local model folder can therefore become a hosted repository, with a new local commit. A native HF snapshot needs its referenced blobs available; the [HF cache guide](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache) explains the snapshot symlinks. Treat an existing folder as a candidate input, not proof of a complete model or its upstream identity.

A second, fresh v0.2.0 instance tested whether supported uploads can also seed a transparent proxy. Its initial data directory contained only SQLite files. The source was our already-downloaded HF 1.8.0 tiny-gpt2 snapshot: nine symlinks resolving to 4,734,064 bytes. The probe copied it into ordinary files and verified hashes; no real Spark/NAS collection was touched.

1. With the instrumented HF upstream blocked, upload the folder through `HfApi.upload_folder` to the non-proxy `library-seed/tiny-gpt2-seed` repository. A fresh client retrieved all nine files with matching hashes and zero upstream requests. The upload created a new local commit; the original HF commit did not resolve to a file in this hosted repository.
2. Create the original `sshleifer` proxy project against the instrumented HF registry, restore upstream access, and request `sshleifer/tiny-gpt2` at its original `5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be` commit. The complete snapshot matched the source. Six Git requests transferred 555,660 response bytes; **there were no upstream LFS/CDN requests or weight downloads**. A second empty client needed zero upstream requests.

The [shared LFS storage wiring](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/apiserver/apiserver.go) and [existing-object checks](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/mirror/tee_cache.go) explain the observed reuse: the hosted upload supplied the content-addressed weights before the proxy acquired the original Git repository. This composition of supported APIs is a measured migration route for this fixture, not a documented bulk importer or native-cache mount. It preserves original proxy repo/commit identity while avoiding repeat weight downloads, but still needs Git data and does not fix the proxy's offline-refresh failure.

[Import evidence](matrixhub-import-evidence.json) records both revisions, file hashes, initial empty-store proof and gateway traffic. The hosted original-revision `model_info` request returned an empty SHA rather than an error; a direct file read returned 404. Import verification must check the actual revision and files, not merely HTTP success. Originals were hash-checked after the run; the disposable import instance was stopped.

### Actual Mac import results

A separate fresh store tested files already downloaded on the Mac, using the same v0.2.0 binary. [Structured evidence](matrixhub-mac-import-evidence.json) records the selected manifests, both hosted commits, six fresh-cache downloads, denied prefetch objects and storage accounting. Originals were read only and rehashed unchanged after all transfers. No weights were fetched from Hugging Face during this exercise.

| Source | Selected contents and provenance | Hosted identity |
|---|---|---|
| LM Studio `lmstudio-community/Qwen3.5-9B-MLX-4bit` folder | 11 files, 5,977,078,858 bytes; two safetensors shards plus configuration, tokenizer and processors. Every selected file matches HF revision `b455506b0f574c74616dbcd56879bde38fafcff3`; all 1,260 indexed tensors exist with valid shard offsets. README and upstream `.gitattributes` were absent locally. | `mac-import/qwen3.5-9b-mlx-4bit` |
| Native HF `unsloth/orpheus-3b-0.1-ft-GGUF` snapshot | One 2,134,077,984-byte `orpheus-3b-0.1-ft-UD-Q4_K_XL.gguf`, matching cached revision `e2b00302c46af8205f521f60016600aa25a068e6`. Its symlink resolves to the newer shared `hub/blobs` store. | `mac-import/orpheus-3b-gguf-selected` |

The importer used supported `HfApi.upload_folder` calls directly against both source folders with explicit filename selections. The native-cache symlink was followed without creating a materialized staging copy. Source verification compared actual content against pinned upstream LFS SHA-256 or ordinary Git blob hashes and sizes. Do not assume a shared-blob filename is the file SHA-256: this GGUF's cache name and content hash differ. An external validation helper rejected a missing indexed shard, a broken symlink and a changed configuration checksum in disposable fixtures. These checks are importer responsibilities demonstrated here, not MatrixHub built-in guarantees.

| Check | Observed result |
|---|---|
| Hosted import with upstream blocked | Both imports succeeded: 12 selected files, 8,111,156,842 bytes. New local commits; original HF commits are not preserved in hosted repositories. |
| Repeat identical import | Zero additional LFS PUTs; object count and weight storage unchanged. Each call created a new Git commit, so import is not strictly idempotent. |
| Fresh hosted downloads | HF 1.8.0 retrieved every selected file with matching hashes and zero upstream requests. |
| Restart, then another empty client with upstream still blocked | HF 2.0.0 retrieved both hosted selections with matching hashes and zero upstream requests. Hosted serving already works offline; the earlier refresh failure concerns proxy repositories. |
| Original upstream repository names and commits | Seeded proxy reads returned the same selected contents. Twelve Git requests transferred 2,110,585 response bytes; zero upstream weight bytes. The gateway allowed only Git metadata routes. |
| Real multi-quant proxy behavior | Orpheus attempted 25 other unique LFS objects totaling 53,537,172,811 bytes. Backend OIDs/sizes matched the unseeded public metadata; all requests were denied before upstream acquisition. No extra weights were downloaded. Qwen's local selection already included all upstream LFS objects. |
| Storage and UI | Four independently SHA-verified LFS objects occupy 8,104,288,451 logical bytes, shared between hosted and proxy identities. The server store occupied about 8.13 GB allocated before the September 27 cleanup. Both hosted file lists render in the UI. Three disposable download rounds used another 24.33 GB logical space and were removed after verification. |

The useful operating mode is explicit selection → independent verification → hosted repository → client download/staging. It gives selected GGUFs and complete sharded-model bundles a browseable, offline-capable home. Mapping the same objects back to original HF names is possible, but reintroduces proxy refresh and unwanted acquisition. The proxy UI listed Orpheus's full 57.1 GB repository size even though only the selected 2.1 GB object was retained; a catalog listing is not proof of local completeness.

This is an import and byte-serving result, not an inference benchmark: no model was loaded, the selected GGUF is not a complete TTS application bundle, and loopback timings do not establish NAS performance. Full inventory, resumable import, hosted-seed deletion/garbage collection, service-independent export/restore and actual consumer integration were left to [#614](https://github.com/kelchm/home-lab/issues/614), which closed as not planned without running them. Keep originals. Private or locally modified artifacts need their own hosted identity and tested access boundary rather than being represented as unchanged public upstream content.

## Athena pull-through evaluation

On September 28, ModelKeep v0.4.12 and HugRS v0.7.1 ran on Athena (DSM 7.3.2, Docker 24.0.2) as disposable Compose projects hardened like production: non-root, read-only root filesystem, all capabilities dropped. Each service joined an internet-connected network and an internal one; test clients joined only the internal network, so a client that tried to bypass the proxy would fail instead of succeeding silently. Clients were `huggingface_hub` 1.8.0 and 2.0.0 with `hf-xet` 1.6.0 (Xet not disabled) and llama.cpp's `light` image (build 11223) using `-hf`. Internet transfer is the byte count received on the proxy container's upstream interface. Hashes were compared with Hugging Face's LFS SHA-256 values. An outage was simulated by detaching the upstream network, which fails fast; a blackholed network was not tested.

### ModelKeep v0.4.12

| Check | Result |
|---|---|
| One file (`config.json`) from `sshleifer/tiny-gpt2` | Only that file acquired; 23.7 KB from the internet |
| Full `tiny-gpt2` snapshot with HF 1.8, then an empty HF 2.0 client | Hashes match; the second download used 0 bytes from the internet |
| One quantization from `Qwen/Qwen2.5-0.5B-Instruct-GGUF` (491 MB of nine variants) | 507 MB from the internet; only that file archived; nothing fetched afterwards |
| `Qwen/Qwen2.5-0.5B-Instruct` (1 GB) cold with HF 1.8, then warm with HF 2.0 | Succeeded on HF 1.8's fifth and final retry after 24 × `503`; warm download 0 bytes; hashes match |
| llama.cpp `-hf`, archived quantization | `/refs`, tree and file served locally; 0 bytes from the internet |
| llama.cpp `-hf`, cold quantization | Failed on the first `503` while the acquisition continued; the third run succeeded; 427 MB in total for a 415 MB file |
| Upstream detached: by commit, by `main`, GGUF, full model, llama.cpp, then a container restart | Everything archived was served |
| Uncached repository while upstream was detached | `503` until the client gave up after about 113 seconds |
| Three concurrent cold clients for a 3.95 GB shard of `Qwen/Qwen2.5-7B-Instruct`, all requesting `main` | One upstream transfer (3.59 GB); both HF 1.8 clients exhausted their retries; HF 2.0 succeeded with the correct hash |
| Upstream detached for 2 minutes during a 3.86 GB transfer | The helper resumed when upstream returned; 4.04 GB in total; hash correct |
| `modelkeep audit`; manifest digests against recorded upstream LFS SHA-256 | Clean; all 20 LFS files match |

Not tested in this run: a second commit of a repository already archived, the same file under another repository, and concurrent requests that mix `main` with its commit SHA. See [what September did not test](#the-archive-contract-and-what-september-did-not-test).

It used about one CPU core while downloading at 150–245 Mbit/s. Docker reported up to 3.5 GiB of memory, which on DSM's cgroup v1 likely includes page cache. A HEAD-driven warm-up that pins the commit and requests each selected file until it is archived worked without the management API.

### HugRS v0.7.1

| Check | Result |
|---|---|
| One file, and one GGUF quantization | Only the requested file fetched; streamed without `503` |
| `Qwen/Qwen2.5-0.5B-Instruct` (1 GB) cold with HF 1.8 | Streamed with no client retries; hashes match |
| llama.cpp `-hf`, cold quantization | Succeeded on the first run |
| Warm download of an archived model | 70–100 KB from the internet per model: every `HEAD`, revision and tree request goes upstream |
| Upstream detached | Every cached model failed: revision requests returned `500` after 10–60 seconds, and one download failed after 418 seconds |
| Three concurrent cold clients for the 3.95 GB shard | One upstream transfer (about 4.15 GB). Two clients received the file without its first 4 MiB chunk, 3,941,247,136 bytes in total. HF 1.8 failed; HF 2.0 resumed the tail and exited 0 with a full-size file whose every offset was shifted by one chunk. HugRS logged no error and its own cached copy was correct |

A code review reproduced the causes with deterministic tests. Completed chunks are tracked for the whole download rather than per client, so a client that joins late never receives chunk 0; this also happens for fully cached files. Mixed range and full-file requests can reorder content. The cache is keyed by repository and path without the revision, and a test served commit A for a request for commit B. Revision, tree and refs metadata is always forwarded upstream with no fallback, and a failed `HEAD` leaves a single-flight entry that later requests wait on indefinitely.

### Code review

Both projects were reviewed against the same rubric: an Opus 5.5 code audit of ModelKeep, then gpt-6-astra architecture reviews of both. ModelKeep's architecture suits a durable mirror. Commit-keyed immutable revisions are stored as ordinary files, responses are built locally, the official client performs acquisition, and one acquisition is shared per request selection. Its confirmed defects follow. "Reproduced" means a unit test against a synthetic upstream; the unmarked ones are source readings. None was observed in the live Athena run, and the two-minute outage there did not exercise the partial-download discard.

- Published bytes are not compared with Hugging Face's recorded LFS SHA-256 or Git blob ID.
- Payload files are not fsynced before a revision is first published; extension and import do fsync.
- A miss under an archived ref acquires upstream's current commit and moves the ref, contrary to its ADR-0012 (reproduced).
- Partial downloads are discarded when the helper reports its own failure, a regression from its commit 0722668 (reproduced).
- Wildcards in a resolve path are passed to the client as file patterns, so one request can acquire a whole repository (reproduced).
- A refresh always downloads the whole snapshot and discards files the archived revision does not list.
- Requests for one file by `main` and by its commit SHA start two upstream transfers (reported from a unit probe that did not assert the count; the two requests were meant to be concurrent but their overlap is unproven; [recovered probe fragment](fixtures/modelkeep-review/mixed-alias-probe-recovered.rs)).
- Revisions are stored separately with no shared content, as its ADR-0001 states. The review noted this in passing and it was not raised as a gap until September 30.
- The download port has no authentication; when trust of the Tailscale capability header is enabled, the management API accepts that header from any client that can reach it.

Each was judged a bounded patch at the time, apart from shared content, which was not assessed. Replacing the cold-miss `503` with streaming would be a multi-week change. HugRS would need a redesigned download session, revision-keyed storage and cached metadata, estimated at several person-weeks, and it has had no commits since July.

## Nexus tradeoffs

[Sonatype's client guide](https://help.sonatype.com/en/configure-hugging-face-with-nexus.html) requires `HF_HUB_DISABLE_XET=1` and says cold files finish caching before being served. [CE limits](https://help.sonatype.com/en/usage-center.html) are 40,000 components and 100,000 requests/day; exceeding them blocks new additions. These differ from H2 database sizing limits, and one model need not equal one component.

[System requirements](https://help.sonatype.com/en/sonatype-nexus-repository-system-requirements.html) recommend 16 GiB for a smallest cloud-native profile targeting 100 requests/second, not a measured homelab minimum. They exclude containerized H2 from supported deployments and recommend PostgreSQL. Resolve that NAS database/resource cost before a trial. No supported no-redownload import from HF caches was established; do not inject objects into Nexus blob storage. HF is [proxy-only](https://help.sonatype.com/en/formats.html), leaving private locally trained models needing another archive/hosting path.

Opus also surfaced [issue #1071](https://github.com/sonatype/nexus-public/issues/1071), an open September 23 report against 3.96.3-01: model file downloads work, but dataset metadata, organization lookups and kernel paths return errors. This is an upstream operator report, not a local reproduction or proof that every required client fails. It reinforces testing actual inference-client behavior rather than equating successful weight downloads with full Hub compatibility. Nexus client credentials and the repository's upstream HF credentials are separate authorization domains.

## Storage and client contract

This section is the September contract. Its definition of "download once" is the narrow one that the [later contract](#the-archive-contract-and-what-september-did-not-test) widened.

“Download once” means a completed retained revision can reach another empty LAN client with zero WAN payload transfer. It does not promise zero metadata requests, zero client copies or coverage of hardcoded external URLs. Retain weights, tokenizer, configuration and indexes; pin commits and verify hashes. Keep retained revisions outside automatic eviction and alert before capacity runs out.

Use a service-owned public cache/library and a separate private archive. [HF_HOME includes token storage](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables); use `HF_HUB_CACHE` for shared model storage instead. Preserve whole cache trees and symlink targets, including newer shared blob directories. Current clients write `CACHEDIR.TAG`: backup tools configured to honor cache exclusions can skip it. Explicitly include any retained library needing backup, and keep irreplaceable artifacts outside disposable-cache policy.

| Consumer | Initial integration |
|---|---|
| SparkRun / vLLM | Configure the actual host download stage before HF imports; preserve local runtime caches and worker sync. Verify a real pinned recipe after library tests. |
| llama.cpp | Stage a verified GGUF by local path with `-m`, or use `-hf` against the endpoint. `-hf` was tested against ModelKeep on Athena, where a cold file failed on the first `503`. |
| ComfyUI / other tools | Use verified paths or the endpoint where supported; plugins with hardcoded HF/CDN URLs need individual coverage. None was tested. |
| LM Studio | No supported endpoint setting was found, and it was not tested. Not an acceptance client. |
| WAN-unavailable runtime | Stage complete files first, then use paths or `HF_HUB_OFFLINE=1`. That variable disables HTTP to the LAN mirror too. |

## Gated repositories and private fine-tunes

Gated/private HF downloads need a separate authenticated boundary and scoped upstream credentials. Upstream authentication does not authorize reads of already-cached bytes. Test anonymous denial, unrelated-user denial, direct file/CAS URLs and revocation against warm content. The public service must not inherit a server token's private access. Shpiel's measured passthrough behavior does not supply this boundary; MatrixHub's project permissions still need qualification.

Local training outputs should be immutable private bundles on Athena: weights/adapters, base-model repository and commit, hashes, training recipe and dataset identity. Use filesystem ACLs and an independent encrypted recovery copy; verify restoration. Promote artifacts explicitly and stage them onto serving hosts. Add S3 or a hosted HF API when a consumer needs it, without delaying preservation. OCI/container publication remains separate.

## Independent research and evidence boundary

A separate Claude Opus 5.5 research run received the requirements without the initial candidate shortlist or recommendation. Execution metadata confirmed `claude-opus-5-5`. It independently recommended a standard NAS-owned HF library and separate backed-up private artifacts, with Olah as an optional disposable frontend. The durable-layer conclusion is supported here; the Olah preference is not adopted because its cache lifecycle conflicts with the retention priority and Shpiel now has local reuse evidence. The independent report did not evaluate Shpiel. Its source-based suggestions do not replace live acceptance.

Shpiel and MatrixHub ran on the Mac with isolated service/client state. MatrixHub additionally imported actual public Mac downloads read only and verified the originals unchanged. No serving workload, real private model, real HF credential store or existing user cache changed; the later [Athena evaluation](#athena-pull-through-evaluation) used disposable NAS instances. Nexus and the other candidates were not executed. MatrixHub passed complete tiny snapshots and selected real large-shard/GGUF imports; actual application integration, disk-full behavior, interruption/power-loss recovery, upgrades and sustained NAS load remain unqualified.

The September 30 followup and code review ran WeightKeep, Muninn and Pulsys fixtures on a workstation only. The October hf-archive runs used an isolated container on Athena and loopback harnesses. None of the later work changed the ModelKeep service or the model library, and none of it tested inference applications, a whole multi-file model through a new server, or native LAN throughput.
