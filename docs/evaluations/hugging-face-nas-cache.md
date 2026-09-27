# Hugging Face downloads retained on Athena

Evaluated September 26, 2026. **Read-only lab inspection, source review and isolated macOS probes; no NAS mirror is deployed or qualified.** [Investigation #608](https://github.com/kelchm/home-lab/issues/608) owns remaining qualification. [Shpiel evidence](hugging-face-nas-cache-evidence.json), [MatrixHub behavior](matrixhub-local-evidence.json), [MatrixHub small import](matrixhub-import-evidence.json), [actual Mac imports](matrixhub-mac-import-evidence.json) and [MatrixHub acceptance gates](matrixhub-gate-evidence.json) record versions, hashes and observed upstream requests.

## Recommendation

Make a retained filesystem library on Athena the foundation. A NAS-side downloader or cache service owns ingestion; LAN consumers download through its HTTP endpoint or stage pinned snapshots locally. Preserve the files independently of the HTTP service's continued availability. Public retention and private training-output preservation must not depend on deploying an OCI registry, S3 service or paid product.

**Shpiel v0.3.1 is the measured public-only baseline, not a selected winner.** Its filesystem layout and measured reuse fit the retention goal, but the [broader community-project survey](hugging-face-cache-landscape.md) found additional candidates that deserve comparison before a NAS pilot: DingoSpeed, standalone hfd, ModelKeep and Bodaay's managed-library approach. Shpiel's private-read authorization, native llama.cpp compatibility, large cold-file delays, recovery and upgrades remain material gaps. Any public Shpiel trial should use one writer, no private upstream token, and a read-only LAN-facing boundary; its write APIs should not be exposed by a public download service.

**Continue MatrixHub v0.2.0 qualification as a curated, verified hosted library.** [Actual Mac imports](#actual-mac-import-results) reused 8.1 GB of existing Qwen MLX and Orpheus GGUF files, served matching files to empty HF 1.8/2.0 clients with upstream blocked, and survived backend restart. Hosted imports also seeded the original proxy identities without fetching weights again. This supersedes the earlier blanket rejection: the measured failures constrain particular workflows, rather than making all use unsuitable.

Transparent proxy use still needs work. Cached proxy reads fail after offline metadata expiry, new proxy ingestion needs external hash verification, and selected-file requests do not bound server prefetch. The real Orpheus test attempted another 53.5 GB of distinct objects; the gateway blocked every unwanted transfer. A hosted repository containing only the selected GGUF avoids that acquisition path and already serves offline. No NAS pilot is qualified yet. Retention/export/restore and actual-client integration are the next checks under [#614](https://github.com/kelchm/home-lab/issues/614); Bodaay remains an alternative if the hosted-library workflow falls short.

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

This is the initial comparison. The [expanded landscape](hugging-face-cache-landscape.md) adds direct mirror servers, NAS download managers and model-distribution systems, with project history and concrete reasons to pursue or deprioritize them. Project age, transparent mirroring and durable retention are separate properties.

| Candidate | Evidence and fit | Disposition |
|---|---|---|
| **[Shpiel](https://github.com/loewenthal-corp/shpiel)** | Apache-2.0; one Go process, filesystem backend, no database required. Repository created July 2026. | Measured public-only baseline; reuse and gaps below. |
| **[MatrixHub](https://github.com/matrixhub-ai/matrixhub/releases/tag/v0.2.0)** | Apache-2.0; September 18 release added SQLite for single-node Compose. Proxy projects and hosted models with permissions. | Continue verified hosted-library qualification: real 8.1 GB imports/reuse/offline restart passed. Transparent proxy still has refresh, selection and verification gaps. |
| **[Nexus CE](https://help.sonatype.com/en/hugging-face-repositories.html)** | Documented models/datasets proxy with local permissions and upstream bearer credentials. Broader repository manager. | Conventional fallback; HTTP-only, buffering, quotas and import cost matter. |
| **[Olah](https://github.com/vtuber-plan/olah)** | MIT; block cache and offline mode. README instructs deleting incompatible caches across upgrades; [issue #85](https://github.com/vtuber-plan/olah/issues/85) reports a missing llama.cpp refs endpoint. | Poor default for permanent retention. That client report was not reproduced locally. |
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

Retain original Spark/NAS files; MatrixHub's internal store is not yet qualified as the sole archive. The two fault-injection services were stopped and their isolated evidence retained. On September 27, all local evaluation services and gateways were stopped, and their model stores, imported copies, disposable client caches, binaries, build trees and virtual environments were removed at the user’s request. Original model downloads and lightweight research records were preserved; future runtime tests require recreating the isolated trial. The next bounded check is service-independent export/restore and retained-byte lifecycle for hosted models. Repair expired-metadata offline proxy reads if retaining original upstream identities becomes the chosen client contract. The broader choice and NAS acceptance remain in #608.

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

This is an import and byte-serving result, not an inference benchmark: no model was loaded, the selected GGUF is not a complete TTS application bundle, and loopback timings do not establish NAS performance. Full inventory, resumable import, hosted-seed deletion/garbage collection, service-independent export/restore and actual consumer integration remain in [#614](https://github.com/kelchm/home-lab/issues/614). Keep originals until that lifecycle is qualified. Private or locally modified artifacts need their own hosted identity and tested access boundary rather than being represented as unchanged public upstream content.

## Nexus tradeoffs

[Sonatype's client guide](https://help.sonatype.com/en/configure-hugging-face-with-nexus.html) requires `HF_HUB_DISABLE_XET=1` and says cold files finish caching before being served. [CE limits](https://help.sonatype.com/en/usage-center.html) are 40,000 components and 100,000 requests/day; exceeding them blocks new additions. These differ from H2 database sizing limits, and one model need not equal one component.

[System requirements](https://help.sonatype.com/en/sonatype-nexus-repository-system-requirements.html) recommend 16 GiB for a smallest cloud-native profile targeting 100 requests/second, not a measured homelab minimum. They exclude containerized H2 from supported deployments and recommend PostgreSQL. Resolve that NAS database/resource cost before a trial. No supported no-redownload import from HF caches was established; do not inject objects into Nexus blob storage. HF is [proxy-only](https://help.sonatype.com/en/formats.html), leaving private locally trained models needing another archive/hosting path.

Opus also surfaced [issue #1071](https://github.com/sonatype/nexus-public/issues/1071), an open September 23 report against 3.96.3-01: model file downloads work, but dataset metadata, organization lookups and kernel paths return errors. This is an upstream operator report, not a local reproduction or proof that every required client fails. It reinforces testing actual inference-client behavior rather than equating successful weight downloads with full Hub compatibility. Nexus client credentials and the repository's upstream HF credentials are separate authorization domains.

## Storage and client contract

“Download once” means a completed retained revision can reach another empty LAN client with zero WAN payload transfer. It does not promise zero metadata requests, zero client copies or coverage of hardcoded external URLs. Retain weights, tokenizer, configuration and indexes; pin commits and verify hashes. Keep retained revisions outside automatic eviction and alert before capacity runs out.

Use a service-owned public cache/library and a separate private archive. [HF_HOME includes token storage](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables); use `HF_HUB_CACHE` for shared model storage instead. Preserve whole cache trees and symlink targets, including newer shared blob directories. Current clients write `CACHEDIR.TAG`: backup tools configured to honor cache exclusions can skip it. Explicitly include any retained library needing backup, and keep irreplaceable artifacts outside disposable-cache policy.

| Consumer | Initial integration |
|---|---|
| SparkRun / vLLM | Configure the actual host download stage before HF imports; preserve local runtime caches and worker sync. Verify a real pinned recipe after library tests. |
| llama.cpp | Stage a verified GGUF by local path initially. Test its native HF downloader separately from Python clients. |
| ComfyUI / other tools | Use verified paths or the endpoint where supported; plugins with hardcoded HF/CDN URLs need individual coverage. |
| WAN-unavailable runtime | Stage complete files first, then use paths or `HF_HUB_OFFLINE=1`. That variable disables HTTP to the LAN mirror too. |

## Gated repositories and private fine-tunes

Gated/private HF downloads need a separate authenticated boundary and scoped upstream credentials. Upstream authentication does not authorize reads of already-cached bytes. Test anonymous denial, unrelated-user denial, direct file/CAS URLs and revocation against warm content. The public service must not inherit a server token's private access. Shpiel's measured passthrough behavior does not supply this boundary; MatrixHub's project permissions still need qualification.

Local training outputs should be immutable private bundles on Athena: weights/adapters, base-model repository and commit, hashes, training recipe and dataset identity. Use filesystem ACLs and an independent encrypted recovery copy; verify restoration. Promote artifacts explicitly and stage them onto serving hosts. Add S3 or a hosted HF API when a consumer needs it, without delaying preservation. OCI/container publication remains separate.

## Independent research and evidence boundary

A separate Claude Opus 5.5 research run received the requirements without the initial candidate shortlist or recommendation. Execution metadata confirmed `claude-opus-5-5`. It independently recommended a standard NAS-owned HF library and separate backed-up private artifacts, with Olah as an optional disposable frontend. The durable-layer conclusion is supported here; the Olah preference is not adopted because its cache lifecycle conflicts with the retention priority and Shpiel now has local reuse evidence. The independent report did not evaluate Shpiel. Its source-based suggestions do not replace live acceptance.

Shpiel and MatrixHub ran on the Mac with isolated service/client state. MatrixHub additionally imported actual public Mac downloads read only and verified the originals unchanged. No NAS service, serving workload, real private model, real HF credential store or existing user cache changed. Nexus and the other candidates were not executed. MatrixHub passed complete tiny snapshots and selected real large-shard/GGUF imports; actual application integration, disk-full behavior, interruption/power-loss recovery, upgrades and sustained NAS load remain unqualified.
