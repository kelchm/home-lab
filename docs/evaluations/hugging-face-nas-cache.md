# Hugging Face downloads retained on Athena

Evaluated September 26, 2026. **Read-only lab inspection, source review and isolated macOS probes; no NAS mirror is deployed or qualified.** [Investigation #608](https://github.com/kelchm/home-lab/issues/608) owns remaining qualification. [Probe evidence](hugging-face-nas-cache-evidence.json) records versions, hashes and observed upstream requests.

## Recommendation

Make a retained filesystem library on Athena the foundation. A NAS-side downloader or cache service owns ingestion; LAN consumers download through its HTTP endpoint or stage pinned snapshots locally. Preserve the files independently of the HTTP service's continued availability. Public retention and private training-output preservation must not depend on deploying an OCI registry, S3 service or paid product.

**Shpiel v0.3.1 is the measured public-only baseline, not a selected winner.** Its filesystem layout and measured reuse fit the retention goal, but the [broader community-project survey](hugging-face-cache-landscape.md) found additional candidates that deserve comparison before a NAS pilot: DingoSpeed, standalone hfd, ModelKeep and Bodaay's managed-library approach. Shpiel's private-read authorization, native llama.cpp compatibility, large cold-file delays, recovery and upgrades remain material gaps. Any public Shpiel trial should use one writer, no private upstream token, and a read-only LAN-facing boundary; its write APIs should not be exposed by a public download service.

**MatrixHub v0.2.0 is a candidate if a full private Hub becomes important.** Its new single-process SQLite deployment fits this NAS better than older MySQL-based descriptions suggest. It has hosted models and project permissions, but source review found offline-refresh and integrity questions. Nexus CE remains a conventional alternative when its database/JVM cost is acceptable; it is no longer the default merely because its documentation is established.

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
| **[MatrixHub](https://github.com/matrixhub-ai/matrixhub/releases/tag/v0.2.0)** | Apache-2.0; September 18 release added SQLite for single-node Compose. Proxy projects and hosted models with permissions. | Private-Hub candidate; source findings need live tests. |
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

## MatrixHub: source findings

The v0.2.0 [Compose deployment](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/deploy/docker-compose.yml) uses one service. [SQLite configuration](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/deploy/config.yaml) keeps the database in its data directory. Its [guide](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/docs/development.md) excludes SQLite on NFS/SMB or shared by multiple processes; direct NAS bind mounts fit.

Proxy setup is per organization/project: the [guide](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/website/docs/guides/mirror-from-huggingface.md) creates a `Qwen` proxy project before requests to `Qwen/...`. [Read handlers](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/apiserver/handler/hf/handler_hf_download.go) check repository permission and can stream in-progress weight downloads. Its route table includes `/refs`. These are source findings, not client acceptance.

- **Offline after refresh expiry:** [synchronization](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/domain/model/model_service.go) propagates remote-pull failures before opening a proxy repo, with a [one-minute TTL](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/internal/domain/model/model.go). This suggests cached reads can fail when refresh is due. Test unavailable upstream after expiry and restart; an immediate warm repeat is insufficient.
- **Integrity and unwanted prefetch:** the pinned [hfd cache](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/mirror/tee_cache.go) queues background objects beyond an immediate file request. Its local persistence path checks length then calls [MovePut](https://github.com/matrixhub-ai/hfd/blob/73bc92d77d19/pkg/lfs/local_storage.go), which renames without hashing. This identifies a missing check, not reproduced corruption. Test same-length wrong content, interrupted transfers and whether selecting one GGUF downloads unrelated variants/history. [Issue #598](https://github.com/matrixhub-ai/matrixhub/issues/598) also requests complete-model verification.

The [roadmap](https://github.com/matrixhub-ai/matrixhub/blob/v0.2.0/ROADMAP.md) places several README-advertised features in later phases, including Xet downloads, S3 and scanning. Qualify the pinned release instead of treating its feature list as shipped behavior.

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

Only Shpiel ran, on the Mac with public/synthetic scratch files. No NAS service, serving workload, real private model, credential store or existing user cache changed. Nexus, MatrixHub and the other candidates were not executed. Complete production snapshots, large shards, actual application integration, disk-full behavior, interruption/power-loss recovery, upgrades and sustained NAS load remain unqualified.
