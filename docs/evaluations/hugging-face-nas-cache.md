# Hugging Face downloads retained on Athena

Evaluated September 26 to October 4, 2026. The goal was that any LAN client sets `HF_ENDPOINT` to Athena, asks for a model by its original Hugging Face name, and each file crosses the internet once and is then kept.

**Outcome, October 4, 2026.** None of the candidates was qualified against the whole contract, and none is selected as the archive. Some failed a gate they were run against; others were never run against every gate. ModelKeep v0.4.12 keeps running unchanged on Athena as a pull-through mirror for public repositories ([#634](https://github.com/kelchm/home-lab/issues/634)). Nothing is being built in its place: the custom prototype was a learning exercise and is retired ([PR #711](https://github.com/kelchm/home-lab/pull/711) closed unmerged, [#710](https://github.com/kelchm/home-lab/issues/710) closed as not planned).

This page records what was tested, what it found, and what a replacement would have to do. Every result describes one pinned version on the dates given. None is a finding that a project cannot work, that its current release still behaves this way, or that writing our own would be cheaper.

Supporting pages:

- [Stock client behavior and the hf-archive slice](hugging-face-sdk-archive-experiment.md): the measurements behind the serving requirements below.
- [Alternatives map](hugging-face-cache-landscape.md): everything surveyed but not run.

## What was done

1. **Survey, September 26.** Documentation and source for mirror servers, download managers and distribution systems.
2. **Workstation fixtures, September 26–30.** Shpiel and MatrixHub ran on macOS loopback against a tiny model, behind a gateway that counted upstream bytes and injected faults. WeightKeep and Pulsys were later exercised against synthetic upstream fixtures; Muninn got an in-process offline failure probe and one diagnostic in which its verifier, on SDK 2.0.0, fetched a valid weight from the live public Hub.
3. **Actual Mac imports, September 27.** 8.1 GB of selected files already downloaded on the Mac were imported into MatrixHub with upstream weight downloads blocked.
4. **Disposable NAS instances with real clients, September 28.** ModelKeep and HugRS ran on Athena, hardened like production. `huggingface_hub` 1.8.0 and 2.0.0 and llama.cpp ran on a network that could reach only the proxy, and internet transfer was counted on the proxy's upstream interface.
5. **A custom slice, October 3–4.** `hf-archive` acquired files with the official SDK, stored each unique file once and served them read-only, to learn what the contract costs to meet.

## Results by candidate

"Source" marks a reading of the code that was not run. Fixture and loopback results show protocol behavior, not production qualification.

| Candidate | How it was tested | What held | What failed or was missing |
|---|---|---|---|
| ModelKeep v0.4.12 | Real clients on Athena; source | Fetched only requested files. Served everything archived, including refs for llama.cpp, with upstream detached and after a restart. One upstream transfer for three concurrent clients of the same ref. Hashes matched Hugging Face | Keeps each revision as its own files, so nothing is reused across commits or repositories (source). A large file not yet archived answers 503: llama.cpp failed on the first one and SDK 1.8 ran out of retries |
| [HugRS](https://github.com/tq02ksu/hugrs) v0.7.1 | Real clients on Athena; source | Fetched only requested files and streamed cold files without 503 | Every cached model failed with upstream detached, because metadata is always fetched upstream. Clients joining a download in progress received the file without its first chunk; an SDK 2.0 client resumed and exited 0 with a full-size corrupt file while the server's own copy was correct |
| [MatrixHub](https://github.com/matrixhub-ai/matrixhub/releases/tag/v0.2.0) v0.2.0 | macOS loopback; a complete tiny snapshot and the 8.1 GB Mac imports | Hosted imports stored each file once and served it offline after a restart. Seeding the proxy from those bytes served the original repository and commit without downloading weights, though it still needed Git metadata upstream | Hosted mode gives every import a new identity. Proxy mode tried to prefetch 53.5 GB of other quantizations for one requested file (blocked by the gateway), failed offline once its metadata expired, and kept an injected same-length corrupt file |
| [Shpiel](https://github.com/loewenthal-corp/shpiel/releases/tag/v0.3.1) v0.3.1 | macOS loopback, tiny files | A second client and an offline restart were served without upstream payload | The refs route llama.cpp uses returned 404. Cold files are buffered whole before serving (source). Reuse across repositories not checked |
| [WeightKeep](https://github.com/afshinghezeli/weightkeep/tree/933b3c9eee70359a0ba7f10b02b3fc2b4ca1e5f3) `933b3c9` | Fixtures with SDK 2.0.0; source | Content reuse across refs and repositories: one upstream fetch for 12 clients over two repositories, none for an unchanged file in a new revision | Stock streaming returned a corrupt early range and let SDK 2.0 assemble a corrupt resumed file. Review widened the hardening needed (below) |
| [Muninn](https://github.com/skibare87/muninn/tree/d11db64fac99b399720bda3d30ee1cac1af29a01) `d11db64` | Two diagnostic scripts; source | A cached file at a pinned commit was served with upstream unreachable | With its SDK moved to 2.0.0 and nothing else changed, its verifier rejected a valid public weight, taking the Xet identifier in a cache filename for the payload SHA-256. Metadata routes answered 502 offline. The model commit was not pinned, so that run cannot be repeated exactly |
| [Pulsys](https://github.com/pulsys-io/pulsys/tree/3699fc08ad5f94e92b838aa7cfbc9399b1e88cf0) `3699fc0` | Two synthetic proxy-route tests only | Nothing beyond those two tests | Identical content under two CDN paths was fetched twice, and a body that did not match its hash was replayed from a warm cache. No real client or NAS |
| `hf-archive`, October 3–4, final source [`9c119dc`](https://github.com/kelchm/home-lab/tree/9c119dc60b4b93725d436e066450ef4af6a2e7f3/tools/hf-archive) | One real shard and a 17 MB model on Athena; fixtures. The October 3 runs predate two review fixes | One 4.1 GB shard recorded under two commits with one stored copy, then served offline to SDK 1.8. Changed bytes on disk were refused | Never run against a representative large whole model or the library |

WeightKeep came closest on storage design. A modified build that waits for verification before serving passed eight fixture cases, and an importer prototype brought selected files in from Hugging Face cache snapshots and plain directories without downloading them. Neither is in its shipped CLI. A review of the same commit, by source and fixtures, widened the work in three directions:

- **Authority containment.** Unrecognized API requests were forwarded upstream with any method and the server's token, with no client check. A private repository could be approved for torrent seeding. Torrent ingestion wrote to paths before validating them.
- **Recovery.** A cached model became unavailable when upstream metadata timed out, although an immediate upstream error fell back to the local copy.
- **Retention.** Garbage collection deleted content that was still referenced or still being pulled. Export accepted same-size corrupt files and followed symlinks out of its destination. Registry protections were described but not connected.

Its storage core, without the torrent, registry and Ollama features, remains a plausible competitor to anything new. Nothing here shows that a new build would be less work.

## Why the first selection missed content reuse

The September tests defined "download once" as a second empty client getting an already archived revision with no internet transfer. ModelKeep passed that. No test asked for a second commit of a repository already held, the same file under another repository name, or one file requested as `main` and by commit SHA at once.

The gap surfaced on September 30 while existing caches were being imported. ModelKeep materializes each revision as its own files, and its ADR-0001 says the archive may duplicate identical blobs across revisions. That is a reading of its design, not a measurement: no second-commit download was run. The reading rests on the reviewer's log from September, because on October 4 the project's GitHub repository returned 404 for reasons not established.

It matters here because revisions of the large models in use overlap substantially. Block sharing on Athena's filesystem can recover the disk afterwards. It cannot avoid the second download.

The lesson: "each unique file once" is a statement about content, so acceptance tests have to vary the commit, the repository and the name while the content stays the same.

## What a replacement would have to do

This is a possible future option, not approved work.

### Acquisition is not ownership

The official SDK is a good way to acquire files: it handles selection, metadata and Xet transfer. It is a dependency, not the authority on what is kept. Everything a client needs later has to live in an archive the service owns, with a read path that does not depend on the acquisition worker, the upstream token or the upstream itself. Each candidate that failed offline had left some of that state upstream.

### Hard requirements

1. **Original identity over the network.** A public-only endpoint serving the original repository, immutable commit and path. MatrixHub's hosted mode worked but renamed everything.
2. **Owned metadata.** Persist the upstream metadata, tree and refs that a fresh client asks for, and record separately which listed files are held. HugRS, MatrixHub's proxy and Muninn each failed offline on metadata while holding the bytes.
3. **Honest partial snapshots.** Acquire only selected files or quantizations and never present a partial revision as complete.
4. **Safe import.** Read both the per-repository cache layout and the newer shared-blob and Xet layout, leave originals untouched, and record hashes and provenance. Never take a cache filename for a hash: that is what broke Muninn's verifier.
5. **One verified store of ordinary files.** Store each file once under our own SHA-256 of its bytes, readable without the service. Check the LFS SHA-256 or Git blob ID that upstream metadata expects as a separate step, and map each repository, commit and path, with those expected IDs, to the stored file. Identical content then shares one copy across repositories, refs and renames. Xet identifiers and cache filenames are not interchangeable with either hash, and an SDK cache is not the durable record.
6. **Coordination keyed by content.** Imports, prefetches and requests for one file under different refs, repositories or names share a single acquisition. An alias must never cause a second download.
7. **Verify, then publish, then claim.** Stage on the same filesystem, check size and identity, publish the file atomically, and only then write a manifest that references it.
8. **Durable state, and refs that move only on purpose.** Jobs, incomplete downloads, metadata and refs survive a restart, a full disk and a failed acquisition in a consistent state. A moving ref such as `main` advances only on an explicit refresh. Ordinary reads, requests for missing files and imports of metadata for an older commit must not move it or roll it back, and a failed refresh leaves the previously published revision usable.
9. **Verify before serving.** Both SDK versions accepted wrong bytes of the right length over plain HTTP, and verifying after streaming let corrupt ranges and resumed files through in WeightKeep. "Ready" means the server has verified the file and can serve it, not just acquired it.
10. **An offline read path.** `HEAD`, `GET`, `Range`, metadata, refs and tree answer correctly for actual clients with empty caches, after a restart, with no token and no upstream.
11. **No automatic deletion at first.** Retention is explicit and owned. WeightKeep's collector deleted content the archive still needed.
12. **Public and private kept apart.** An upstream token is not authorization for LAN clients, and private training outputs need their own boundary.

### A bounded first slice

The smallest useful version is explicit import and prefetch, serving only files already verified, with no fetching on a client's request. `hf-archive` explored that shape at small scale. It would not be a full replacement for the proxy, because a client asking for something not yet held gets nothing.

Readiness after a restart belongs in that slice. Files already acquired may be hashed again before they are served, and until then clients get 503, run out of retries or quietly fall back to an older cached file. A completed prefetch is therefore not readiness, and the cost of verifying a whole library is unknown.

Adopting such a slice would be justified by these outcomes, which are design criteria and not a planned run: a representative whole model reaches actual Hugging Face clients and llama.cpp through the endpoint, from empty caches, after a restart; content already held is reused across a new ref, a second repository and a rename while imports and prefetches run concurrently; a bad payload of the right size, bad ranges and a ref rollback are refused; and the service resumes after a full disk or a crash and becomes ready for the whole library in a workable time.

Transparent fetching on a cold request is a separate, larger design choice. There is no clean "not ready yet" answer for these clients: llama.cpp failed on the first 503, the SDK retries a 503 on `HEAD` but not on `GET`, and its two tested versions disagree about `Retry-After`. ModelKeep answers 503 until a file is archived, which is what those clients tripped over; HugRS streams at once, and its streaming path is where the corruption was. The [client measurements](hugging-face-sdk-archive-experiment.md#how-stock-clients-behave-when-a-server-is-not-ready) are the starting point for that decision.

Leave out inference, torrent distribution, registries, Ollama adaptation and other protocols unless a consumer needs them.

## Not qualified

Complete snapshots were run only at small scale: MatrixHub with a nine-file tiny-gpt2, and ModelKeep with a tiny model and a 1 GB Qwen model. Not covered:

- The later replacement candidates (WeightKeep, Muninn, Pulsys) and `hf-archive` against a representative large whole model, and no candidate with real clients against a representative complete changed or later revision of a model already held, as opposed to the one real shard the prototype served under two commits, or against the NAS library.
- ModelKeep with a second commit, a second repository or mixed `main` and commit requests, run live.
- Native LAN throughput, and inference applications end to end.
- Power loss. Faults were injected and processes stopped, but no machine lost power.
- How long a server takes to re-verify a whole library before it is ready after a restart.
- Fetching on a cold request at a realistic file size in a custom gateway or replacement. Earlier trials included multi-gigabyte cold requests from the SDK and smaller GGUF requests from llama.cpp, with the mixed results above; the `hf-archive` gateway saw only a 720,896-byte file on loopback.
- Real gated or private workflows, with qualified access control on cached reads. An earlier synthetic private-authorization fault probe did run, but no genuine gated or private model or full read boundary was qualified.

Existing Spark and Vonk cache contents stay as they are. Nothing evaluated here is qualified as their sole copy.
