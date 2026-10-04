# Hugging Face cache alternatives map

Surveyed September 26 and 30, 2026. This is a map of what exists around the [NAS cache evaluation](hugging-face-nas-cache.md), so a later search does not start from nothing. The candidates that were run are covered there. Everything on this page was assessed from documentation or source at the date or commit shown and was **not run**, so each entry is a lead with a reason, not a verdict on the project today.

Three different things reduce repeat downloads: a mirror that clients reach through `HF_ENDPOINT`, a library of files kept on the NAS and used by path, and a system that distributes models between machines. Only the first matches the goal as stated.

## Mirrors reached through HF_ENDPOINT

| Project | As read | Why it was not taken further |
|---|---|---|
| [Olah](https://github.com/vtuber-plan/olah) | 0.5.2 at `c70b2f8`. History back to 2023, refs routes and offline handling for pinned revisions | Own block format keyed by repository, commit and path. The README tells operators to delete incompatible caches before upgrading |
| [DingoSpeed](https://github.com/dingodb/dingospeed) | `f2ae437`. Go service with offline mode, prewarming and a standalone mode | Block-file storage that needs a FUSE reader to yield ordinary files. No releases |
| [hfd](https://github.com/matrixhub-ai/hfd) | `0c711cf`. MatrixHub's headless daemon, usable alone | Still scans repository refs and prefetches their large files in the background |
| [guilt/xet-server](https://github.com/guilt/xet-server) | v1.1.0. Native Xet proxy and server | Its [mirroring guide](https://github.com/guilt/xet-server/blob/22c4aa1df566041b804aee2d4b1df1b97e2f04df/docs/MIRRORING.md#91-important-what-the-proxy-can-and-cannot-cache) says pull-through can relay uncached content straight from the CDN, so proxying does not guarantee retention |
| [Pulp HF plugin](https://github.com/pulp/pulp_hugging_face) | `d267eca`. On-demand files inside Pulp | Metadata requests are forwarded upstream without caching. A whole platform to run |
| [hftools](https://github.com/ziozzang/hftools) | Plain files, upstream-hash verification, cache import, hardlink deduplication across repositories | Indexes existing downloads, one revision per repository, and does not fetch on demand. Deduplicates after download |
| [Artifact Keeper](https://github.com/artifact-keeper/artifact-keeper) | `86b567c`. Hosted and proxy repositories on a filesystem | Proxy stores content by repository and request path |
| [Mini-HF](https://github.com/realtyz/mini-hf) | `3d8d33f`. LAN distribution with a management UI | Needs PostgreSQL, Redis and S3. Files are namespaced by repository |
| [AIMirror](https://github.com/livehl/aimirror) | `b2dcce1` | Cache key is a hash of the URL, and upstream is contacted before the cache |
| [olah-go](https://github.com/zukadong/olah-go) | Source only | Stores by repository, commit and path. Its author calls it immature |
| [ModelID](https://github.com/dongfangzhizhu/ModelID) | `eea57c5` | Proxy rejects `HEAD` and buffers whole files in memory |
| [Nexus CE](https://help.sonatype.com/en/hugging-face-repositories.html) | Documented Hugging Face proxy in a general repository manager | Its [client guide](https://help.sonatype.com/en/configure-hugging-face-with-nexus.html) requires Xet disabled and caches cold files whole before serving. CE has [component and request limits](https://help.sonatype.com/en/usage-center.html). Proxy only, with no import from existing caches found |

[Artifactory](https://jfrog.com/blog/native-xet-support-in-jfrog-artifactory/) supports Hugging Face only in [paid editions](https://docs.jfrog.com/installation/docs/feature-comparison-matrix-for-self-mangaged-jpds). [cacheserver/huggingface](https://github.com/cacheserver/huggingface) and [zcc35357949/hf-mirror](https://github.com/zcc35357949/hf-mirror) are small mirrors with no recent maintenance. Several search results are not servers at all: [hf-mirror-site](https://github.com/padeoe/hf-mirror-site) is a public relay's site, [light-hf-proxy](https://github.com/shiyemin/light-hf-proxy) and [aliendao](https://github.com/git-cloner/aliendao) are clients for someone else's mirror, and [Mirrors-Project/hf-mirror](https://github.com/Mirrors-Project/hf-mirror) is an Olah fork.

Two things recur in the source as read. Several mirrors key storage by repository and path or by URL, and we found no layer in them that reuses identical content across repositories. Some forward metadata upstream on every request, which rules out offline use.

## Libraries kept on the NAS and used by path

These download on request and keep ordinary files or a standard cache tree. Consumers read the files over NFS or stage them locally. None replaces huggingface.co for a client that expects an endpoint. [HFDesk](https://github.com/bashrusakh/hfdesk) is a fork of Bodaay and [blackbeardlabs/hf-downloader](https://github.com/blackbeardlabs/hf-downloader) is a younger download manager with more moving parts; neither adds a distinct option.

| Project | As read | Boundary |
|---|---|---|
| [Bodaay HuggingFaceModelDownloader](https://github.com/bodaay/HuggingFaceModelDownloader) | [v3.2.0](https://github.com/bodaay/HuggingFaceModelDownloader/releases/tag/v3.2.0). Go binary and container, web UI, standard cache, file and revision selection, resume, SHA-256 verification. The longest history in this group | Its "proxy" is an outbound proxy setting and its "mirror" copies cache directories. Its v3 migration recommends re-downloading for the new layout |
| [HuggingHack](https://github.com/tyedalwaves/HuggingHack) | NAS model browser, existing-folder indexing and uploads | Young, and its private features are untested |
| [Native HF cache](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache) | The official client writing to a shared tree, with no server | The fallback if a transparent endpoint stops being essential |

A small sample of operator posts on X, found on September 26 and not reproduced, described this approach in use: an NFS-mounted Hugging Face cache shared across vLLM hosts or Sparks to stop per-machine copies filling local disks. That shows people do this, not that multi-writer use is safe. If a shared tree is ever used, share `HF_HUB_CACHE` and not `HF_HOME`, which [also holds the token](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables#hfhome). Note that `HF_HUB_OFFLINE=1` stops requests to a LAN mirror as well as to the internet.

## Distribution between machines

| Project | As read | Cost here |
|---|---|---|
| [ModelExpress](https://github.com/ai-dynamo/modelexpress) | v0.6.0. Model cache and transfer service with [NVIDIA Dynamo integration](https://docs.dynamo.nvidia.com/dynamo/knowledge-base/kubernetes/model-loading/model-caching) | gRPC and engine integration, not an endpoint. Needs Redis or Kubernetes metadata, and can fall back to downloading from Hugging Face directly |
| [Dragonfly](https://github.com/dragonflyoss/dragonfly) | Established P2P distribution with an [`hf://` client](https://d7y.io/docs/next/operations/integrations/hugging-face/) | Its own client plus scheduler and peers. Peer caches are evictable, so an authoritative copy is still needed |
| [hf-mount](https://github.com/huggingface/hf-mount) | Hugging Face's lazy FUSE or NFS view of remote repositories | Its cache defaults to about 10 GB under `/tmp`. It gives remote access, not retention |

These address moving models to many machines, which is not the current problem. They become relevant if distribution across the Sparks does.

## Full hubs

[KohakuHub](https://github.com/KohakuBlueleaf/KohakuHub), [CSGHub](https://github.com/OpenCSGs/csghub) and [Shardline](https://github.com/STEXS-Technologies/shardline) host models locally. Hosting is a different job from retaining every upstream download, and each brings more infrastructure than a cache needs: KohakuHub runs on LakeFS, S3 and a database; CSGHub's community edition [limits sync sources](https://opencsg.com/docs/en/csghub/101/function/reposync/reposync_intro) to OpenCSG; Shardline's [Hub API is beta](https://github.com/STEXS-Technologies/shardline/blob/main/docs/COMPATIBILITY_STATUS.md). They become relevant only if private training outputs need a hosted home, which the main evaluation keeps separate from the public cache.

## When to revisit an entry

Nothing on this page was run, so it supports no verdict on what any project can do. An entry is worth running when its pinned release appears to keep one copy of identical content across repositories and commits, serve its own metadata and refs offline, and verify bytes before a client receives them. Those three are a screen, not the contract: acquiring only selected files, importing existing caches, owned retention and recovery are also in the [requirements](hugging-face-nas-cache.md#hard-requirements), and a feature list is not evidence until that release is run against them.
