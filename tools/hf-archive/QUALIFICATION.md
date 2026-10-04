# hf-archive qualification

Recorded October 3, 2026, for the first slice of `hf-archive`. This reports what was run and observed. It does not decide whether the prototype replaces the ModelKeep proxy on the NAS; ModelKeep was left running and untouched throughout.

The NAS and Spark results were produced by the source in this directory mounted into a container, not by an image built from it: the offline import and disk-full runs used a base Python 3.12 container with the source mounted, which needs no SDK, and the server ran in an earlier build of the image, which supplies the dependencies, with the package from the working tree mounted over the installed one. Timings are single runs, not benchmarks.

## Test suite

`uv run pytest` passes 101 tests on Python 3.12 with `huggingface_hub` 2.1.1, and `uv run ruff check` is clean. Core tests use synthetic content of at most 29 MB and no network. The integration tests start the real server on a loopback socket and drive it with the unmodified SDK using fresh client caches, with outbound connections other than loopback blocked. They cover:

- A fresh SDK client after a server restart on a read-only archive: pinned commit and last known `main`, model info, refs, tree, paths-info, `hf_hub_download` and a selected `snapshot_download`.
- Ranges and resume served only from verified blobs; a retained blob corrupted on disk is refused before any range bytes are sent.
- A corrupt or interrupted upstream download publishes nothing and does not poison the retry.
- A file that is not acquired does not poison the client cache: the same client cache succeeds after a later acquisition.
- Concurrent pulls of the same weight under two repositories and two names acquire one object; an import waits for a concurrent pull of the same content.
- Offline imports from a native HF snapshot and from a plain directory with a renamed file reuse existing objects.
- A publication failure keeps the old ref and leaves only valid manifests.
- A 12-second first-use verification with the SDK's default 10-second timeouts ends in success or an honest retryable error, never unverified bytes.

Regression tests exist for the defects an independent review found while the slice was being written: same-size wrong bytes under a correct blob name being reused, a tampered Git alias substituting other valid content, quarantine racing between two acquisitions of the same bytes, undurable directory creation, response header injection through a request path, and malformed `Content-Length` values.

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
