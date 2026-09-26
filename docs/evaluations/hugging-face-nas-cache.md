# Hugging Face downloads retained on Athena

Evaluated September 26, 2026. **Source review and read-only NAS inspection only; no mirror is deployed or qualified.** [Investigation #608](https://github.com/kelchm/home-lab/issues/608) owns the bounded live evaluation and its acceptance evidence.

## Recommendation

Use a NAS-hosted pull-through service for public Hugging Face models, with its persistent state bind-mounted directly from Athena's filesystem. Nexus Repository CE is the first candidate to qualify: it has a documented HF proxy and client configuration, but its resource cost, cold-download behavior and actual client compatibility still need measurement. It is not yet a production selection.

Keep an access-controlled archive for locally trained models and adapters alongside this service. Cache retention and private artifact preservation have different failure consequences. A private artifact archive is useful immediately and must not depend on the mirror evaluation, a model registry or an S3 deployment finishing first. Required capabilities must have no paid-license dependency.

## Local evidence

The September 26 read-only SSH inspection of Athena returned:

| Property | Observed value |
|---|---|
| Architecture | `x86_64` |
| RAM | 32,071 MiB total; 29,894 MiB available at inspection |
| `/volume1` | `df -h`: 49T total, 20T used, 29T available |
| Docker Engine | 24.0.2 |
| Docker Compose | 2.20.1-6047-g6817716 |

This establishes capacity for a trial, not sustained throughput or an acceptable service memory budget. The [architecture reference](../architecture.md) identifies the DS1821+, disk pool and storage network. The current [NAS workload model](../../synology/README.md) is explicitly applied Compose; [#379](https://github.com/kelchm/home-lab/issues/379) tracks the unimplemented shared deployment plane.

[SparkRun](../../sparks/inference/sparkrun/README.md) presently downloads into `/opt/spark-cache/huggingface` and synchronizes to the worker. Its model-loading cache can remain local while the NAS supplies downloads. Recipe environment settings do not by themselves prove that SparkRun's earlier host-side download process uses the mirror.

[The Vonk cache PR](https://github.com/kelchm/home-lab/pull/529) describes retained data on Athena and the Sparks. It is evidence of existing data to preserve, not evidence that a general HF service exists or that those objects can be dropped into another cache's internal storage.

## Candidate comparison

| Candidate | Evidence and fit | Verdict |
|---|---|---|
| **Nexus Repository CE** | [HF models/datasets proxying is supported in CE](https://help.sonatype.com/en/hugging-face-repositories.html). Standard HF client endpoint configuration is documented. It is a broader repository manager with a database and JVM. | First conventional candidate for an isolated trial. |
| **Olah** | Lightweight, on-demand block cache with offline mode. Its [README](https://github.com/vtuber-plan/olah) warns that caches cannot migrate between versions and instructs deletion on upgrade. [Issue #85](https://github.com/vtuber-plan/olah/issues/85) reports a missing endpoint in llama.cpp's native downloader. | Poor default for long-lived retention across heterogeneous clients; the reported llama.cpp issue is not a local reproduction. |
| **`guilt/xet-server` / `xet-proxyd`** | [Upstream](https://github.com/guilt/xet-server) claims native Xet pull-through caching and offline handoff. Repository created September 11; latest inspected release is [v1.1.0](https://github.com/guilt/xet-server/releases/tag/v1.1.0), September 16. | Promising protocol fit, but very new. Upstream tests do not establish lab compatibility or durability. Revisit if conventional proxy limitations are unacceptable. |
| **JFrog Artifactory** | Vendor documents [native Xet support](https://jfrog.com/blog/native-xet-support-in-jfrog-artifactory/) and hosted/remote HF repositories. Its [self-managed package matrix](https://docs.jfrog.com/installation/docs/feature-comparison-matrix-for-self-mangaged-jpds) excludes HF from the free editions. | Excluded by the zero-paid-license requirement. |
| **Native HF cache on a NAS share** | [HF's cache layout](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache) preserves revisions and reuses downloaded blobs. A NAS-side downloader can populate it, with readers mounting it or staging verified snapshots locally. | Simpler retention fallback; explicit prefetch/staging replaces transparent HTTP pull-through. |

Download managers that populate or synchronize an HF cache are not automatically compatible servers for `HF_ENDPOINT`. Likewise, a generic reverse proxy may relay Hub metadata while redirected weight downloads bypass it.

## Nexus tradeoffs that matter here

[Sonatype's client guide](https://help.sonatype.com/en/configure-hugging-face-with-nexus.html) requires `HF_HUB_DISABLE_XET=1`: Nexus does not yet support native Xet. It also documents that a cold file is served only after it has finished caching. Large first downloads can therefore time out and require longer client timeouts or explicit prewarming. Serving already-cached bytes is the important repeat case, but the initial experience still needs qualification with representative shard sizes.

The intended client shape is a local `HF_ENDPOINT` plus disabled Xet in the process actually performing downloads. These variables must be set before importing the HF client. `HF_HUB_OFFLINE=1` is different: it prevents the client from making HTTP requests, including requests to the LAN mirror. Reserve it for runtimes whose required files are already staged. See [HF environment variables](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables).

[CE limits](https://help.sonatype.com/en/usage-center.html) are currently 40,000 components and 100,000 requests per day; exceeding either prevents new components being added. Measure HF accounting rather than treating one model as one component. [System requirements](https://help.sonatype.com/en/sonatype-nexus-repository-system-requirements.html) recommend 16 GiB for their smallest cloud-native profile, which targets 100 requests/second; this is not a measured homelab requirement. They also explicitly exclude containerized H2 from supported deployments and recommend PostgreSQL. A production choice must resolve that database shape and its NAS resource cost; a one-container example is insufficient evidence. If PostgreSQL is selected, it and its data must also run on Athena to meet the host-local requirement.

## What “download once” must mean

For a completed, retained repository revision, a second client with an empty local cache should receive its payload entirely from the NAS. Clients may still retain local copies and transfer bytes over the LAN. This does not promise zero upstream metadata requests, zero transfers after an interrupted initial download, or automatic interception of applications using hardcoded HF URLs or another registry.

Use immutable revision identities and retain complete required snapshots: weights alone omit tokenizers, configuration, indexes and other dependencies. Keep retained payloads out of automatic cleanup policies, alert before the volume fills, and explicitly test upgrades and restart recovery. Refuse new writes cleanly at capacity rather than evicting retained models. Deduplication across different repositories or quantizations is a separate property to measure, not implied by a warm-cache hit.

Nexus [cache-age settings](https://help.sonatype.com/en/configurable-repository-fields.html) govern revalidation, and its blocked-upstream mode can serve cached components. Neither is sufficient proof that an entire HF snapshot can be fetched with the WAN unavailable. The trial must use empty client caches and verify both content hashes and upstream payload bytes.

Existing Spark/HF caches and Vonk objects require an inventory and verified import path. Do not write files directly into Nexus blob storage. If supported cache seeding is unavailable, preserve a native HF archive and stage existing models from there; adopting a proxy must not force re-downloading the retained collection merely to populate its database.

## Gated models and private fine-tunes

These are two distinct extensions:

- **Gated/private content hosted on HF:** Nexus documents an [upstream HF bearer token](https://help.sonatype.com/en/create-a-hugging-face-repository.html) for gated downloads. Qualify the selected private repositories too. Use a separate authenticated repository/access boundary and scoped credentials; clients authorized for the public cache must not inherit the upstream token's private access. Revocation and cached-content access need testing as well as initial authentication.
- **Models/adapters created locally:** Nexus's [HF format supports proxy repositories, not hosted ones](https://help.sonatype.com/en/formats.html). Preserve these as immutable private artifact bundles on the NAS, recording base-model revision, adapter/weight hashes, training recipe and dataset identity. Keep publication explicit, stage bundles to the serving host and load by local path. Maintain a recovery copy on another device and test restoration. An S3 interface can be added when a consumer needs it; native HF upload/API hosting would be a separate future requirement.

Public models are reproducible inputs; private training outputs may be irreplaceable and contain source-derived information. Give the archive its own ACLs and backup policy, separate from the public cache. Container/OCI image publication is another protocol and remains outside this HF evaluation.

## Evidence boundary

No Nexus, Olah or Xet server was run during this review. No live client was redirected, credential copied, existing cache imported, service restarted or model file changed. The open investigation holds the live compatibility, no-redownload and recovery checks; this document records the findings and constraints rather than a production runbook.
