# hf-archive: an SDK-based archive experiment

Run October 3–4, 2026. `hf-archive` was a prototype that used the official `huggingface_hub` SDK to acquire selected model files, verified them, stored each unique file once, and served them read-only to stock clients through `HF_ENDPOINT`. It was built to learn what an archive meeting the [later contract](hugging-face-nas-cache.md#the-archive-contract-and-what-september-did-not-test) would involve, after the [followup evaluation](hugging-face-cache-followup.md) and the [WeightKeep code review](weightkeep-code-review.md).

**Outcome: the prototype is retired as a learning exercise.** It was never deployed, it was not qualified against the NAS library, and maintaining it is declined. [PR #711](https://github.com/kelchm/home-lab/pull/711) is retired without merging and [#710](https://github.com/kelchm/home-lab/issues/710), which would have qualified it against the whole library, is closed as not planned. No adoption work follows. ModelKeep ran unchanged on Athena throughout.

This page keeps what the experiment taught, because the findings about stock client behavior apply to any server put in front of these clients. Timings are single runs on loopback or over an SSH relay; none is a throughput figure.

## What was built

The source is in PR #711 at commit [`9c119dc`](https://github.com/kelchm/home-lab/tree/9c119dc60b4b93725d436e066450ef4af6a2e7f3/tools/hf-archive); it is not in this repository's `main`.

- `pull` and `import` acquired explicitly selected files. Bytes were staged, checked for exact size and LFS SHA-256 or Git blob ID, published into a global SHA-256 store of ordinary files, and only then recorded in a revision manifest and ref. Public, ungated repositories only.
- `serve` answered the model info, refs, tree, paths-info and resolve routes from the archive alone: no upstream calls, no token, no write routes, no Xet signals. A blob was hashed before its first use in each process, and unverified bytes were not sent.
- A cold request was never fetched from upstream. Files had to be acquired ahead of use.

115 tests passed at `9c119dc` on Python 3.12 with `huggingface_hub` 2.1.1. They drove the real server with the unmodified SDK on loopback and covered restart, ranges, corrupt and interrupted upstreams, concurrent acquisition of one weight under two repositories, offline imports, and `ENOSPC` and `SIGKILL` injected around each publication rename. That count includes tests for two defects found in review of the pull request and fixed at `9c119dc`: a blob whose hash failed with a read or permission error was re-hashed on every request instead of being refused, and an offline import of saved metadata for an older commit moved `main` back. Both fixes are covered by tests. The October 3 NAS runs predate them. The October 4 run against Athena used `9c119dc` and so included them, but it did not set out to exercise either: it moved no ref with an offline import and caused no read or permission error.

## October 3: a real 4.1 GB shard on the NAS

These runs used the source as it stood before the two review fixes, mounted into a container on Athena, not an image built from it.

| Check | Result |
|---|---|
| Live pull of `prajjwal1/bert-tiny` at `6f75de8b60a9f8a2fdf7b69cbd86d9e64bcb3837` | `config.json` (285 bytes) and `pytorch_model.bin` (17,756,393 bytes, SHA-256 `dab2c2bddcfb48ea430ef63fd76d46d67d704487844d967256a50dd7d7fd0a66`); 2 of 5 files recorded. The only acquisition from the live Hub |
| Offline import, container without network: `model-00001-of-00120.safetensors`, 4,113,768,668 bytes, SHA-256 `46cc9e99897500fee92f2f08c95665cda51a704f835548465a1023cd4db63ef7`, under commit `25a44fdbf16862a46b7cc9921142c6c81350af2f` of `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` | Imported; manifest holds 1 of 144 files |
| The same shard under commit `9eaebb7c4e96d983dcd538e18624622ba5b820a8` (`main`) | Recorded as a reuse in 9.128 s, the time to re-read and re-verify the stored blob. No second object written; both manifests reference one blob |
| `huggingface_hub` 1.8.0 on a Spark host, empty cache, server with no route to upstream | Model info, refs, a 144-file tree, paths-info, then the 4.1 GB shard with the correct SHA-256, twice (67.189 s and 66.305 s over an SSH relay) |
| The same client at the older pinned commit | Resolved to the blob already in the client cache; no second 4.1 GB transfer |
| `huggingface_hub` 2.1.1 on macOS, empty cache | `bert-tiny` metadata, the LFS file with the correct SHA-256 and a selected snapshot, in 1.194 s |
| `bytes=0-63` and `bytes=-64` of the shard | 206 with the correct bytes and `Content-Range` |
| Startup verification of the archive holding the shard | About 8.6 s, zero failures |
| Disk full: 25,165,824-byte import onto a 32 MiB tmpfs on the NAS | Real `ENOSPC` after 4,194,304 bytes; staging empty afterwards, old ref and manifest preserved, retry succeeded once space was available |
| Seven injected faults on the development machine: `ENOSPC` and `SIGKILL` before each of the blob, manifest and ref renames, and `SIGKILL` after the manifest rename | Existing refs byte-identical, published manifests referenced only verified blobs, the next acquisition cleared the dead staging and completed |

The public API returned 401 for the GLM repository at the time, so its metadata came from upstream identities saved earlier. This shows import from recorded metadata, not that current metadata for that repository can be fetched. Afterwards the archive held 3 content files totalling 4,131,525,346 bytes and 3 manifests, and `verify` on a read-only mount checked 4 references with no problems.

## October 4: what stock clients do when a server is not ready

A loopback fixture served one 139,264-byte file to unmodified `huggingface_hub` 1.8.0 and 2.1.1 on Python 3.12.13, over plain HTTP with no SDK patching and no timeout overrides. 42 cases ran, each with a new empty cache unless noted. [Result matrix](fixtures/hf-archive-education/sdk-probe-summary.json). "Available at N s" means the fixture started answering normally N seconds after the first request.

| Server behavior | SDK 1.8.0 | SDK 2.1.1 |
|---|---|---|
| `HEAD` headers withheld, available at 12 s or 30 s | Success at 12.3 s and 30.3 s, 2 `HEAD`s | Success at 12.3 s and 30.1 s, 2 `HEAD`s |
| `HEAD` 503 with `Retry-After: 1`, available at 6 s | Success at 7.3 s | Success at 6.1 s |
| Same, available at 12 s | Success at 15.3 s, 6 `HEAD`s | Fails at 10.1 s, 7 `HEAD`s |
| Same, available at 30 s | Fails at 23.3 s | Fails at 10.1 s |
| `HEAD` 503 forever, no `Retry-After` | Fails at 23.2 s | Fails at 23.1 s |
| `HEAD` 503 forever, `Retry-After: 4` | Fails at 23.2 s | Fails at 25.1 s |
| `HEAD` answered at once, `GET` headers withheld to 12 s or 30 s | Fails at 10.3 s with `ReadTimeout`, 1 `GET` | Success at 12.1 s and 30.1 s, 2 and 3 `GET`s |
| `HEAD` and `GET` headers at once, body withheld to 12 s or 30 s | Success, 2 and 3 `GET`s | Success, 2 and 3 `GET`s |
| `GET` 503 with `Retry-After: 1` | Fails at 0.2 s, 1 `GET` | Fails at 0.07 s, 1 `GET` |
| `HEAD` 503 for `main`, older file already cached | Returns the cached file, 1 `HEAD`, no error | Same |
| Pinned commit already cached | No requests | No requests |
| New pinned commit, empty cache, `HEAD` 503 forever with `Retry-After: 1` | Fails at 23.1 s | Fails at 10.1 s |
| Wrong bytes of the advertised length | Reports success; the file does not hash to its name | Same |

What follows for a server in front of these two versions:

- **The 10-second defaults are per-request read timeouts, not a deadline for the download.** On an empty cache a failed metadata `HEAD` is repeated once with a 60-second timeout and retries enabled ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1170-L1190), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1118-L1125)), which is why a `HEAD` that stays silent for 30 seconds still succeeds.
- **A 503 on `HEAD` is retried a fixed number of times, not for a fixed time.** Without `Retry-After` both versions gave up after about 23 seconds. 2.1.1 honors an integer `Retry-After` and sleeps that value plus one second, without the 8-second cap on its own backoff ([source](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/utils/_http.py#L527-L569)); 1.8.0 ignores the header ([source](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/utils/_http.py#L394-L491)). A short `Retry-After` therefore shortens how long 2.1.1 waits in total, and no single retry window holds across versions.
- **A 503 on the payload `GET` is not retried by either version.** The `GET` retries only 429, and 408 in 2.1.1 ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L392-L399), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L363-L370)).
- **The plain HTTP download path checks the received size, not the advertised digest** ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L486-L495), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L439)). Both versions stored same-length wrong bytes under a blob named for the expected SHA-256. A server cannot rely on these clients to catch corruption over plain HTTP. This was not tested on Xet transfers.
- **A client holding an older file for a moving ref gets that file back, without an error, when the `HEAD` fails.** The fixture did not advance `main`, so this shows the fallback exists, not how stale its result can be.

Blocking `HEAD` works because of this SDK's 60-second second attempt, and early `GET` headers work because of its body retry. In this fixture neither was tried with other clients, with a stream that stalls after some bytes, or with a trickling body. The [Athena re-run](#october-4-re-run-against-athena-from-the-workstation) later cut a connection mid-transfer, which is a different condition; a stream that stalls without closing stayed untested.

## October 4: fetching on a cold request

A gateway kept outside the package wrapped it unmodified: a `resolve` request for a file the archive did not hold started a worker running the existing acquisition code, and answered 503 with `Retry-After: 2` until the worker had published the verified blob. It ran against a loopback fake Hub with SDK 2.1.1 and a 720,896-byte file: 10 scenarios, 120 checks, all passing, in 37 seconds. [Recorded results](fixtures/hf-archive-education/cold-coordinator-summary.json).

Where a worker delay is named, it is a sleep injected after the worker's upstream download finished and before verification. It stands in for an acquisition that takes that long. It is not a slow transfer.

What worked:

- Six concurrent cold clients across two repositories started two workers, one per requested file. The content lock reduced that to one upstream payload `GET` and one stored blob. A renamed copy in a third repository needed no upstream `GET`, and the other quantization in the repository was never fetched.
- After the gateway restarted with the upstream down, clients with empty caches were served held content by `main` and by pinned commit.
- A corrupt same-size upstream payload was rejected by the worker. The client got 502 and no file, no blob was published, and the existing ref and files were unchanged.

What did not, or only partly:

- Answering every pending `HEAD` at once with 503 covered only the shorter delays. With injected delays of 0, 6 and 12 seconds the first client succeeded at 3.1, 9.1 and 15.1 seconds. At 30 seconds it failed at 15.1 seconds and succeeded on its next call, once the worker had finished.
- Holding each `HEAD` for up to 8 seconds before the 503 made the 30-second case succeed on the first call, at 30.4 seconds. No longer delay was tried. Answering `HEAD` at once and holding the `GET` instead worked for a 6-second delay and failed at 8.4 seconds for a 12-second one, because the `GET` 503 is not retried.
- A failed or stopped acquisition still publishes metadata: the revision's full tree, and a ref the archive did not have, can appear before any payload does.
- Only `resolve` triggered an acquisition. API calls for a repository the archive had never seen answered 404, so a cold `snapshot_download`, which lists files first, was not covered.
- The gateway was stopped with `SIGTERM` while a worker sat in the injected delay, after its download. No payload blob was published. This was a graceful stop, not `SIGKILL`, a cut mid-transfer or power loss, and it does not qualify the gateway against a hard crash.

## October 4: re-run against Athena from the workstation

The `bert-tiny` archive and the source at `9c119dc` were copied to an isolated directory on Athena and served from a base Python 3.12.13 container: read-only, on a Docker internal network, with no token and no route to upstream. Clients reached it through an SSH relay. The model library was not touched. [Records](fixtures/hf-archive-education/nas-workstation/).

- Fresh clients fetched the weight with the correct SHA-256 plus a selected two-file snapshot: SDK 1.8.0 in 1.456 s, SDK 2.1.1 in 1.570 s.
- **Resume inside one call, observed on the wire.** A proxy cut the first `GET` after 12,582,912 of 17,756,393 bytes. The same SDK 2.1.1 call sent `Range: bytes=10485760-`, the server answered 206 with the remaining 7,270,633 bytes, and the file had the correct SHA-256. Two payload `GET`s carried 19,853,545 bytes in total, so about 2 MiB was fetched twice.
- **A leftover partial file is treated differently by each version.** The correct first 1 MiB of the weight was placed by hand as `<etag>.incomplete` in an otherwise empty cache. SDK 1.8.0 finished with the correct SHA-256, consistent with its source appending to a shared incomplete file ([source](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1812-L1813)); no request transcript was kept, so its `Range` request was not observed. SDK 2.1.1 ignored the seed, downloaded the whole file and left the seed in place, matching its source, which writes to a new uniquely named temporary file on every call ([source](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1995-L2003)). This is a seeded-file check, not a recovery from a real interruption across calls.
- **Corruption on disk is refused and repaired in place.** One byte of the weight was changed in the NAS copy, keeping its size, and the server restarted. `HEAD`, `GET` and a ranged `GET` answered 500 and `/healthz` reported `warm_failures: 1`. After the byte was restored, `bytes=0-63` and `bytes=-64` returned 206 with the right bytes, without a restart.
- A final `verify` checked 2 references with no problems. The test container, its network, the image pulled for it and the task directory were then removed. ModelKeep was healthy before and after, with its start time unchanged at `2026-09-29T11:44:07Z`.

## What this taught

- **The SDK is a useful acquirer and nothing more.** It handles download, file selection and metadata. It is not a LAN server and it is not a retention authority: refs, offline API responses, content-keyed coordination, verified publication and retention all had to be built around it.
- **Verification belongs before bytes leave the server.** The clients accept wrong bytes of the right length, and the WeightKeep review showed that verifying after streaming lets corrupt early ranges and resumed files through.
- **There is no clean "not ready yet" answer for these clients.** `HEAD` is the only request they retry on 503, the two versions disagree about `Retry-After`, and the approaches that worked depend on undocumented retry behavior. A server that fetches on a cold request inherits that problem for every large file. Acquiring ahead of use is not enough on its own for a server that re-hashes after a restart: a file it already holds still answers 503 until its hash finishes, and a client can exhaust its retries or fall back to an older cached commit of a moving ref in that time. Files have to be acquired and the server has to finish verifying them before clients use it. How long that takes for a whole archive was not measured beyond the 8.6 seconds for one 4.1 GB shard.
- **Storing each unique file once and reusing it across commits worked** on one real 4.1 GB shard, with ordinary files that stay readable without the service.

## What was never established

- Scale: one 4.1 GB shard, one 17 MB model and synthetic files. Not a whole multi-file model and not the NAS library.
- Throughput on the LAN. Every client run went through a relay or loopback.
- Permanent retention: there was no removal or garbage collection, and power loss was not tested.
- Clients other than `huggingface_hub` 1.8.0 and 2.1.1: no `transformers`, llama.cpp, LM Studio, `hf` CLI or Git clients.
- Gated or private content: acquisition refused such repositories by design.
- Access control: the server had none, so every client that could reach it could read everything it held.
- Fetching on a cold request as a supported feature, at any realistic file size.

## Evidence

The JSON under [`fixtures/hf-archive-education/`](fixtures/hf-archive-education/) is the recorded output, unmodified, and still names the original temporary paths. The NAS cleanup record lists the Docker commands run on Athena.

The harness scripts, dependency pins, the gateway's exact answer policy and the source at `9c119dc` are not included in this checkout. They are in an external bundle: `hf-archive-education-20261004.tar.gz`, 188,797 bytes, SHA-256 `a654feec5f490b41dfb622c2171e4b892d0318e2855dc77d334fcc16cbdec157`. The operator's copy is at `/Users/kelchm/Downloads/` on the workstation; no public download location is provided. It holds no model payloads, caches or credentials. A reference copy of its [README](fixtures/hf-archive-education/bundle-README.md) is kept here and lists the contents of the tarball, not of this checkout, and what was adapted so the scripts run from an extracted copy; the bundle's `manifest.json` records the size and SHA-256 of every file.

Re-running the two loopback harnesses requires a copy of that tarball, which for the operator means taking it from the workstation location above; the repository alone is not enough. With a copy in the current directory, `uv` installed and network access for the pinned Python and dependencies:

```sh
# Confirm the copy matches the recorded SHA-256 before extracting.
shasum -a 256 hf-archive-education-20261004.tar.gz

tar -xzf hf-archive-education-20261004.tar.gz
cd hf-archive-education-20261004

# SDK probe: 42 cases. Pass the uv binary itself, not a version-manager shim.
./sdk-probe/setup-and-run.sh /absolute/path/to/uv

# Cold-request experiment: 10 scenarios.
(cd tools/hf-archive && uv sync --locked --dev --python 3.12.13)
tools/hf-archive/.venv/bin/python cold-coordinator/harness.py
```

A re-run is a new run to compare with the recorded results, not a replay. [Packaging validation](fixtures/hf-archive-education/bundle-package-validation.json) re-ran one coordinator scenario and one SDK case from a fresh extraction, not all 42 cases and 120 checks. The Athena run is not reproduced by the bundle: its scripts name the original host and ports, and repeating it means recreating the isolated container and pulling the public `prajjwal1/bert-tiny` files again.

Without the bundle, the committed results can still be inspected, and the descriptions above are meant to be enough to build equivalent probes. Those would be new tests, not a reproduction of the recorded harness.
