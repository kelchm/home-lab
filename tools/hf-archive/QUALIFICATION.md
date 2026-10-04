# hf-archive qualification

Recorded October 3, 2026, for the first slice of `hf-archive`. This reports what was run and observed. It does not decide whether the prototype replaces the ModelKeep proxy on the NAS; ModelKeep was left running and untouched throughout.

The NAS and Spark results were produced by the source in this directory mounted into a container, not by an image built from it: the offline import and disk-full runs used a base Python 3.12 container with the source mounted, which needs no SDK, and the server ran in an earlier build of the image, which supplies the dependencies, with the package from the working tree mounted over the installed one. Timings are single runs, not benchmarks.

## Test suite

`uv run pytest` passes 115 tests on Python 3.12 with `huggingface_hub` 2.1.1, and `uv run ruff check` is clean. Core tests use synthetic content of at most 29 MB and no network. The integration tests start the real server on a loopback socket and drive it with the unmodified SDK using fresh client caches, with outbound connections other than loopback blocked. They cover:

- A fresh SDK client after a server restart on a read-only archive: pinned commit and last known `main`, model info, refs, tree, paths-info, `hf_hub_download` and a selected `snapshot_download`.
- Ranges and resume served only from verified blobs; a retained blob corrupted on disk is refused before any range bytes are sent.
- A corrupt or interrupted upstream download publishes nothing and does not poison the retry.
- A file that is not acquired does not poison the client cache: the same client cache succeeds after a later acquisition.
- Concurrent pulls of the same weight under two repositories and two names acquire one object; an import waits for a concurrent pull of the same content.
- Offline imports from a native HF snapshot and from a plain directory with a renamed file reuse existing objects.
- A publication failure keeps the old ref and leaves only valid manifests.
- A 12-second first-use verification with the SDK's default 10-second timeouts ends in success or an honest retryable error, never unverified bytes.
- An offline import of saved metadata for an older commit, after a pull recorded `main` at a newer one, leaves `main` unchanged and reports it; `--move-refs` moves it, and both commits stay served by hash.

Regression tests exist for the defects an independent review found while the slice was being written: same-size wrong bytes under a correct blob name being reused, a tampered Git alias substituting other valid content, quarantine racing between two acquisitions of the same bytes, undurable directory creation, response header injection through a request path, and malformed `Content-Length` values.

Two defects found in review of the pull request were fixed after the runs recorded below, and are covered by tests only. Neither was re-run on the NAS, and the NAS and client results below predate both changes.

- A blob whose hash failed with an I/O or permission error left no result, so every request hashed it again and answered 503 `VerificationPending`. The failure is now remembered for the file as it is on disk and answered with 500 `ArchiveInconsistent`. The tests cover a read error (one hash attempt across repeated requests and two verification passes, `warm_failures` steady at 1, recovery when the file is replaced), a blob that cannot be opened by the request or by the hashing thread, a blob removed or replaced while its hash was starting, and that refused requests leave no file descriptor open.
- An `import --metadata` from a saved document moved every ref the document listed, so importing an older commit rolled `main` back. It now creates missing refs and keeps recorded ones unless `--move-refs` is given. The tests cover the default, the flag, creation of a missing ref, an import at the commit a ref already names, a pull and a live-metadata import still moving the ref, and the reported `refs` and `refs_retained`.

## Live acquisition from the Hub

`hf-archive pull prajjwal1/bert-tiny --include config.json --include pytorch_model.bin` against huggingface.co with SDK 2.1.1 resolved `main` to commit `6f75de8b60a9f8a2fdf7b69cbd86d9e64bcb3837` and acquired `config.json` (285 bytes, regular Git file) and `pytorch_model.bin` (17,756,393 bytes, LFS, SHA-256 `dab2c2bddcfb48ea430ef63fd76d46d67d704487844d967256a50dd7d7fd0a66`). The revision manifest records 2 of 5 files; the other three stayed not acquired. The repository reports `private: false` and `gated: false`.

This is the only acquisition from the live Hub. It establishes that the SDK download path, verification and publication work end to end for one small public model.

## Offline import on the NAS

The NAS runs were made in a container with networking disabled, into a fresh archive root, from sources mounted read-only, including the 4.1 GB shard.

- Source: one real shard, `model-00001-of-00120.safetensors`, 4,113,768,668 bytes, SHA-256 `46cc9e99897500fee92f2f08c95665cda51a704f835548465a1023cd4db63ef7`, independently re-hashed before the run.
- Metadata: `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw`. The public API returned 401 for this repository at the time of the run, so the metadata documents were built from upstream identities saved earlier. This shows offline import from recorded metadata. It does not show that current upstream metadata for this repository can be fetched.
- Commit `25a44fdbf16862a46b7cc9921142c6c81350af2f`: imported the shard (`via: import`); manifest holds 1 of 144 files.
- Commit `9eaebb7c4e96d983dcd538e18624622ba5b820a8` on `refs/heads/main`: the same shard was recorded with `via: reuse` in 9.128 seconds, the time to re-read and re-verify the stored blob, with no second object written. Both manifests reference one ordinary SHA-256 blob.
- `prajjwal1/bert-tiny` was also imported on the NAS from a directory using metadata fetched live: 2 of 5 files, the same two blobs as the live pull.

## Serving from the NAS to real clients

The server ran on the NAS with no HF token and on a Docker internal network; an outbound connection from the container to `1.1.1.1:443` failed. Clients were restricted to the archive endpoint.

- SDK 1.8.0 on a Spark host, fresh cache: model info, refs, a 144-file tree and paths-info for the GLM repository at `main`, then `hf_hub_download` of the 4,113,768,668-byte shard and a selected `snapshot_download`. The downloaded file hashed to the expected SHA-256. Wall time 67.189 seconds including the client-side hash, over an SSH loopback relay; this is not a LAN throughput figure. A second run with another empty cache, after the server was restarted on the final source, gave the same result in 66.305 seconds.
- SDK 2.1.1 on macOS, empty cache, after the server was restarted on the final source: `bert-tiny` metadata (5 files), refs, tree, paths-info, the 17,756,393-byte LFS file with the correct SHA-256, and a selected snapshot, in 1.194 seconds.
- Old pin: the same SDK 1.8.0 client, reusing the cache from that second run, ran `hf_hub_download` at the pinned commit `25a44fdb`. It resolved to the blob already in the client cache, with no second 4.1 GB transfer.
- Ranges: direct `GET` requests to the NAS server for `bytes=0-63` and `bytes=-64` of the shard each returned 206 with the correct first and last 64 bytes, and `Content-Range` of `bytes 0-63/4113768668` and `bytes 4113768604-4113768667/4113768668`.
- Startup verification of the archive holding the 4.1 GB shard took about 8.6 seconds with zero failures, taken from the server log between start and the end of verification (01:44:44.649 to 01:44:53.295). After the final restart `/healthz` reported `warm: ready`, `warm_failures: 0`, 2 repositories.

## Archive state after the runs

The NAS archive held 3 ordinary files in the content store, 4,131,525,346 bytes in total, and 3 revision manifests: two for the GLM repository at 1 of 144 files each, and one for `bert-tiny` at 2 of 5. Staging was empty. `hf-archive verify`, run with the archive mounted read-only and networking disabled, checked 4 references and reported no problems.

## Failure tests on real filesystems

- Disk full: on a 32 MiB tmpfs on the NAS, a 25,165,824-byte import hit a real `ENOSPC` after 4,194,304 bytes had been copied. The command reported `NoSpaceError`, staging was empty afterwards, the old ref and its one manifest were preserved, the old blob verified after a restart, and the retry succeeded once space was available.
- Kill and injected disk full: seven fault cases. `ENOSPC` is injected immediately before the rename that publishes a blob, a manifest and a ref; the acquisition process is killed with `SIGKILL` immediately before each of those three renames; and it is killed immediately after the manifest rename. Kills after the blob or ref rename are not tested. In every case existing refs were byte-identical afterwards, every published manifest referenced only verified blobs, the old revision was still served, and the next acquisition cleared the dead process's staging and completed. Publication is atomic per document, not one transaction: a kill between the manifest and ref renames leaves a valid new manifest beside the old ref, which the next acquisition completes. These run on the development machine's filesystem, not on the NAS.

## What this does not establish

- Scale. One 4.1 GB shard, one 17 MB model and synthetic files. Not a whole multi-file model, not the full NAS library, not a file near the SDK's 50 GB plain-HTTP limit.
- Throughput and latency on the LAN. No native-network download benchmark was taken.
- Warm-up cost for a large archive. Startup verification reads every referenced blob; only the 4.1 GB case was timed.
- First-use verification beyond the SDK's retry window. A blob that takes longer to hash than the client keeps retrying fails for a fresh client and silently serves an older cached commit of a moving ref for a client that has one. The operating procedure in the README (restart, wait for ready) avoids it; nothing enforces it.
- Client coverage. `huggingface_hub` 2.1.1 and 1.8.0 download paths only. No `transformers`, llama.cpp, LM Studio, `hf` CLI or `git` clients, and no datasets or Spaces.
- Gated or private content, which the slice refuses by design, and per-client access control, which it does not have.
- Crash consistency under power loss. Kill and disk-full were tested at the points listed above; pulling power was not.
- Retention. There is no garbage collection or removal, so long-term capacity behaviour is untested.

## Educational follow-up, October 4, 2026

The prototype is now treated as a learning exercise more than as a likely long-term replacement for ModelKeep. This section records what a further day of workstation-only testing taught, so the lessons outlast the prototype. It is not a deployment endorsement, and nothing here changes the source: the package is at commit `9c119dc`, the 115 tests were last run green at that commit, and only this document and the README changed afterwards. Everything above this section is the October 3 record, left as written.

The harnesses, dependency pins and result files for these runs were produced outside the repository and are kept as a separate bundle, `hf-archive-education-20261004.tar.gz`, not committed here. Each subsection describes what was run closely enough to build an equivalent probe. The exact harnesses, including the experimental gateway's entry points and answer policy, exist only in the bundle; [Re-running from the bundle](#re-running-from-the-bundle) gives the commands. Timings are single runs on loopback or over an SSH relay, with tiny files; none is a throughput figure.

### What stock clients do when the server is not ready

A loopback fixture served one 139,264-byte file to unmodified `huggingface_hub` 1.8.0 and 2.1.1 wheels on Python 3.12.13, each case with a new empty cache unless noted, over real HTTP with no SDK patching and no timeout overrides. 42 cases ran, reported as 23 rows per version. "Available at N s" means the fixture started answering normally N seconds after the first request.

| Server behaviour | SDK 1.8.0 | SDK 2.1.1 |
| --- | --- | --- |
| `HEAD` headers withheld, available at 12 s or 30 s | success at 12.3 s and 30.3 s, 2 `HEAD`s | success at 12.3 s and 30.1 s, 2 `HEAD`s |
| `HEAD` 503 with `Retry-After: 1`, available at 6 s | success at 7.3 s | success at 6.1 s |
| same, available at 12 s | success at 15.3 s, 6 `HEAD`s | fails at 10.1 s, 7 `HEAD`s |
| same, available at 30 s | fails at 23.3 s | fails at 10.1 s |
| `HEAD` 503 forever, no `Retry-After` | fails at 23.2 s | fails at 23.1 s |
| `HEAD` 503 forever, `Retry-After: 4` | fails at 23.2 s | fails at 25.1 s |
| `HEAD` answered at once, `GET` headers withheld to 12 s or 30 s | fails at 10.3 s with `ReadTimeout`, 1 `GET` | success at 12.1 s and 30.1 s, 2 and 3 `GET`s |
| `HEAD` and `GET` headers at once, body withheld to 12 s or 30 s | success, 2 and 3 `GET`s | success, 2 and 3 `GET`s |
| `GET` 503 with `Retry-After: 1` | fails at 0.2 s, 1 `GET` | fails at 0.07 s, 1 `GET` |
| `HEAD` 503 with `Retry-After: 1` for `main`, older file already cached | returns the cached file, 1 `HEAD`, no error | same |
| Pinned commit already cached | no requests | no requests |
| New pinned commit, empty cache, `HEAD` 503 forever with `Retry-After: 1` | fails at 23.1 s | fails at 10.1 s |
| Wrong bytes of the advertised length | reports success; file does not hash to its name | same |

What that means in practice:

- The documented 10-second defaults (`HF_HUB_ETAG_TIMEOUT` and `HF_HUB_DOWNLOAD_TIMEOUT` in the [environment variable reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables)) are per-request read timeouts, not a limit on the whole download. On an empty cache a failed metadata `HEAD` is repeated once with a 60-second timeout and retries enabled ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1170-L1190), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1118-L1125)), which is why a `HEAD` that simply does not answer for 30 seconds still succeeds.
- A 503 on `HEAD` is retried a fixed number of times, not for a fixed time. Without `Retry-After` both versions gave up after about 23 seconds. 2.1.1 honours an integer `Retry-After` and sleeps that value plus one second, without the 8-second cap that applies to its own backoff ([`_http.py`](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/utils/_http.py#L527-L569)); 1.8.0 ignores the header ([`_http.py`](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/utils/_http.py#L394-L491)). So a short `Retry-After` shortens how long 2.1.1 waits in total, a long one lengthens it, and there is no single wall-clock "retry window" a server can design to across versions.
- A 503 on the payload `GET` is not retried by either version: the `GET` retries only 429 (and 408 in 2.1.1) ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L392-L399), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L363-L370)). For these two versions, a 503 meant as "not ready, try again" is retried only on `HEAD`.
- The plain HTTP download path checks the received size, not the digest the server advertised ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L486-L495), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L439)). Both versions stored same-length wrong bytes under a blob named for the expected SHA-256. An archive serving these clients over plain HTTP cannot lean on them to catch corruption, so it needs its own verification before serving. This was observed on the HTTP path only, not on Xet transfers.
- A client holding an older cached file for a moving ref gets that file back, without an error, when the `HEAD` fails. The fixture did not advance an upstream `main`, so this shows the fallback exists, not how stale its result can be.

Blocking `HEAD` works because of this SDK's 60-second second attempt, and early `GET` headers work because of its body retry. Neither was tried with other clients, with a stream that stalls after some bytes, or with a trickling body.

### Fetching on a cold request, as an experiment

The package still acquires only by explicit `pull` or `import`. To learn what a cold fetch would involve, an experimental gateway kept outside this directory wrapped the unmodified package: a `resolve` request for a file the archive did not hold started a worker running the existing acquisition code, and answered 503 `AcquisitionPending` with `Retry-After: 2` until the worker had published the verified blob. It ran against a loopback fake Hub with SDK 2.1.1 clients and a 720,896-byte file: 10 scenarios, 120 checks, all passing, in 37 seconds. It was never part of the package and is not a shipped feature.

Where a scenario below names a worker delay, that is a sleep injected into the worker after its upstream download has finished and before verification. It stands in for an acquisition that takes that long; it is not download time, and none of these figures says anything about throughput.

What worked:

- A cold `resolve` started the acquisition and the client received only verified bytes. Six concurrent cold clients across two repositories started two workers, one per requested file, and the existing content lock reduced that to one upstream payload `GET` and one stored blob, recorded as one pull and one reuse. A renamed copy in a third repository needed no upstream `GET`. The other quantization in the repository was never fetched.
- After the gateway restarted with the upstream down, fresh clients were served held content by `main` and by pinned commit, and the stock read-only `serve` answered from the same archive. `hf-archive verify` found no problems.
- A corrupt same-size upstream payload was rejected by the worker (exit 3); the client got 502 and no file, no blob was published, and the existing ref and the already-acquired `config.json` were unchanged. When the upstream `main` then moved to a new commit with a corrupt payload, the recorded `main` stayed on the old commit, and once the upstream healed the same client got verified bytes for the pinned commit.
- The gateway was stopped with `SIGTERM` while a worker sat in that injected delay. No payload blob was published and the file stayed not acquired; the restarted gateway repeated the download, and the next acquisition swept the leftover staging. The worker records the revision's metadata before it fetches the payload, so that metadata can remain after the stop, as in the failure case below; the harness checked only for the blob and the not-acquired entry. This was a graceful stop, not `SIGKILL`, not a cut mid-stream and not power loss.

What did not work, or only partly:

- Answering every pending `HEAD` at once with 503 and `Retry-After: 2` covered only the shorter delays tried. With an injected worker delay of 0, 6 and 12 seconds the first client succeeded at 3.1, 9.1 and 15.1 seconds; at 30 seconds it failed at 15.1 seconds, and succeeded on its next call once the worker had finished. The 3-second floor comes from this client version waiting `Retry-After` plus one second; it is what this setup produced, not a guarantee.
- Holding each `HEAD` for up to 8 seconds before the 503 was enough for the same 30-second delay: the first call succeeded at 30.4 seconds. No longer delay was tried. Answering `HEAD` at once from metadata and holding the `GET` instead worked for a 6-second delay and failed at 8.4 seconds for a 12-second one, because the `GET` 503 is not retried.
- A failed acquisition still publishes metadata: the revision's full tree, and a ref the archive did not have yet, can appear before any payload does. Existing refs and content are preserved, but "nothing changes on failure" is not true of metadata.
- Only `resolve` triggered an acquisition. API calls for a repository the archive has never seen answered 404, so a cold `snapshot_download`, which lists files first, was not covered.

Some of the "no payload before publication" checks are weaker than they look: in scenarios where the client gave up on `HEAD` it never sent a `GET`, so there was nothing to refuse. The corrupt-payload scenario and the server's verification gate are the real evidence for that property.

Not shown: files of realistic size, formats other than the synthetic one, acquisitions that outlive a gateway restart, or how a cold request for a large model behaves. The one observation in that direction is the 30-second injected delay above, where the first call failed under the immediate-503 policy and a later call succeeded; no acquisition of realistic size or duration was run, so nothing here predicts what a real one would do.

### Re-run on the NAS from the workstation

`hf-archive pull prajjwal1/bert-tiny` was repeated on the workstation with SDK 2.1.1 and gave the October 3 result: commit `6f75de8b`, `config.json` (285 bytes) and `pytorch_model.bin` (17,756,393 bytes, the same SHA-256), 2 of 5 files. That archive and the package source at `9c119dc` were copied to an isolated directory on the NAS and served from a base Python 3.12.13 container: read-only, on a Docker internal network, with no HF token, and with an outbound connection to `1.1.1.1` failing. The original model library was not touched; every step used copies. No Spark host was involved.

Before teardown, `hf-archive verify` inside the test container checked 2 references and reported no problems. Cleanup then completed: the test container, its network, the Python image pulled for the test and the task directory were removed from the NAS, and the workstation relay was stopped. ModelKeep was healthy before and after, with its start time unchanged at `2026-09-29T11:44:07Z`.

- Fresh clients on the workstation fetched the weight with the correct SHA-256 plus a selected two-file snapshot: SDK 1.8.0 in 1.456 seconds, SDK 2.1.1 in 1.570 seconds. On this NAS the container's published port was not reachable while it sat on the internal network, so the clients reached the server through an SSH relay to a loopback port. These are not LAN figures.
- The two SDK versions treat a leftover partial file differently. The test placed the correct first 1 MiB of the weight by hand as `<etag>.incomplete` in an otherwise empty cache; it was not left by an interrupted download. After a server restart, SDK 1.8.0 consumed that seed and finished with the correct SHA-256 in 1.444 seconds, consistent with its source, which appends to a shared incomplete file ([source](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1812-L1813)). No request transcript was kept for that run, so the `Range` request itself was not observed. SDK 2.1.1 ignored the same seed, downloaded the whole file correctly, and left the seed in place, so the run's expectation that the seed would be consumed failed and is recorded as failed. That matches its source, which writes to a new uniquely named temporary file on every call ([source](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1995-L2003)). This is a compatibility check with a seeded file, not an observed recovery from a real interruption across calls.
- SDK 2.1.1 does resume inside one call, and here the requests were captured. A loopback proxy in front of the relay cut the first `GET` after 12,582,912 of 17,756,393 bytes; the same `hf_hub_download` call, with default timeouts and an empty cache, sent `Range: bytes=10485760-`, the server answered 206 with `Content-Range: bytes 10485760-17756392/17756393`, and the finished file had the correct SHA-256. Two payload `GET`s carried 19,853,545 bytes in total, so about 2 MiB was fetched twice.
- One byte of the weight was changed in the NAS copy, keeping its size, and the server restarted. `HEAD`, `GET` and a ranged `GET` all answered 500 `ArchiveInconsistent`, and `/healthz` reported `warm_failures: 1`. After the byte was restored and the file hashed correctly again, `bytes=0-63` and `bytes=-64` returned 206 with the right bytes and `Content-Range`, without a restart.
- The first attempts to start the container failed for a reason unrelated to the prototype: the ACL on the task directory let the owning DSM account's UID and groups read it, and the two other container UIDs tried could not. The container was then run as that account. An ACL entry for another identity was not tried.

### One correction

`hub.py` still sets `HF_XET_CHUNK_CACHE_SIZE_BYTES=0` by default during acquisition. The SDK documentation now states that this variable [no longer has any effect](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables) and that `hf_xet` no longer keeps a local chunk cache for downloads; the lockfile pins `hf_xet` 1.6.0. The setting is harmless and was left in the source, and the README no longer describes it as disabling anything.

### Re-running from the bundle

`hf-archive-education-20261004.tar.gz` holds the harness scripts, dependency pins, recorded results and the exact package source at `9c119dc`. It holds no model payloads, client caches or virtual environments. From the extracted directory, with `uv` installed and network access for the pinned Python and dependencies:

```sh
tar -xzf hf-archive-education-20261004.tar.gz
cd hf-archive-education-20261004

# SDK probe: 42 cases against a loopback fixture. Pass the uv binary itself, not a version-manager shim.
./sdk-probe/setup-and-run.sh /absolute/path/to/uv

# Cold-request experiment: 10 scenarios against a loopback fake Hub.
(cd tools/hf-archive && uv sync --locked --dev --python 3.12.13)
tools/hf-archive/.venv/bin/python cold-coordinator/harness.py
```

Both use loopback only once their dependencies are installed, need no credentials, and overwrite the result files in the extracted copy. The bundle's README and `manifest.json` list what was adapted so the scripts run outside their original paths.

The NAS run is not reproduced by the bundle. Its scripts are kept as a record and name the original host and ports. Repeating it means recreating the isolated container and network on a NAS and pulling the pinned public `prajjwal1/bert-tiny` files again from the live Hub.

### Still not established

Everything in the October 3 list above, and additionally: cold fetching as a supported feature; large or multi-file models through any cold path; resume on files larger than 17 MB or after more than one interruption; clients other than `huggingface_hub` 1.8.0 and 2.1.1; and native LAN throughput. The 4.1 GB NAS results above are from October 3 and were not repeated.
