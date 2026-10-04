# Stock client behavior and the hf-archive slice

Run October 3–4, 2026. `hf-archive` was a small prototype built to learn what the [archive contract](hugging-face-nas-cache.md#what-a-replacement-would-have-to-do) costs to meet. It is retired and was never deployed: [PR #711](https://github.com/kelchm/home-lab/pull/711) is closed unmerged and [#710](https://github.com/kelchm/home-lab/issues/710), which would have qualified it against the whole library, is closed as not planned.

This page keeps the measurements, because how stock clients behave applies to any server put in front of them. The requirements drawn from them are in the [main evaluation](hugging-face-nas-cache.md#hard-requirements). Timings are single runs on loopback or through an SSH relay, not throughput figures.

## What the slice was

The final source is in PR #711 at [`9c119dc`](https://github.com/kelchm/home-lab/tree/9c119dc60b4b93725d436e066450ef4af6a2e7f3/tools/hf-archive), not in `main`. The October 3 runs on Athena predate two review fixes; the October 4 runs used the final source.

- **Acquire.** `pull` and `import` took explicitly selected files from public repositories using `huggingface_hub` 2.1.1. Bytes were staged, checked for exact size and LFS SHA-256 or Git blob ID, published into one store of ordinary files named by SHA-256, and only then recorded in a revision manifest and ref.
- **Serve.** A read-only server answered model info, refs, tree, paths-info and resolve from the archive alone, with no token and no upstream calls. It hashed each file before first use in a process and sent nothing unverified.
- **No cold fetch.** A file had to be acquired before a client asked for it.

## What it showed on real bytes

- **One copy under two commits.** A 4.1 GB shard imported on Athena under one commit was recorded under a second in about 9 seconds, the time to re-verify it. No second file was written.
- **Offline serving to real clients.** `huggingface_hub` 1.8.0 on a Spark, with an empty cache, fetched metadata, a 144-file tree and the shard with the correct SHA-256 from a server with no route upstream. On October 4 a 17 MB model was served the same way from an isolated container on Athena to SDK 1.8.0 and 2.1.1 on the workstation.
- **Corruption refused, then repaired in place.** With one byte changed on disk, `HEAD`, `GET` and ranged `GET` answered 500. Restoring the byte restored service without a restart.
- **Interrupted publication.** A real disk-full error on Athena, and disk-full and kill faults injected around each publication step on the development machine, left existing refs intact and manifests pointing only at verified files.

## How stock clients behave when a server is not ready

A loopback fixture served one 139,264-byte file over plain HTTP to unmodified `huggingface_hub` 1.8.0 and 2.1.1, each case starting from an empty cache unless noted. "Available at N s" means the fixture began answering normally N seconds after the first request.

| Server behavior | SDK 1.8.0 | SDK 2.1.1 |
|---|---|---|
| `HEAD` headers withheld, available at 12 s or 30 s | Succeeds | Succeeds |
| `HEAD` 503 with `Retry-After: 1`, available at 6 s | Succeeds at 7.3 s | Succeeds at 6.1 s |
| Same, available at 12 s | Succeeds at 15.3 s | Fails at 10.1 s |
| Same, available at 30 s | Fails at 23.3 s | Fails at 10.1 s |
| `HEAD` 503 forever, no `Retry-After` | Fails at 23.2 s | Fails at 23.1 s |
| `HEAD` 503 forever, `Retry-After: 4` | Fails at 23.2 s | Fails at 25.1 s |
| `GET` headers withheld to 12 s or 30 s | Fails at 10.3 s | Succeeds |
| `GET` headers sent, body withheld to 12 s or 30 s | Succeeds | Succeeds |
| `GET` 503 with `Retry-After: 1` | Fails at once | Fails at once |
| `HEAD` 503 for `main`, older file already cached | Returns the cached file, no error | Same |
| Wrong bytes of the advertised length | Reports success | Reports success |

What follows:

- **The 10-second defaults are per-request read timeouts, not a deadline for the file.** On an empty cache a failed `HEAD` is repeated once with a 60-second timeout ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1170-L1190), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1118-L1125)), which is why a silent `HEAD` still succeeds at 30 seconds.
- **A 503 on `HEAD` is retried a fixed number of times, not for a fixed time.** 2.1.1 honors `Retry-After` and 1.8.0 ignores it ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/utils/_http.py#L527-L569), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/utils/_http.py#L394-L491)), so no single retry window holds across versions.
- **A 503 on the payload `GET` is not retried by either version** ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L392-L399), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L363-L370)).
- **Plain HTTP downloads check the size received, not the advertised digest** ([2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L486-L495), [1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L439)). Both stored wrong bytes under a name that claims the expected SHA-256. Xet transfers were not tested.
- **A client holding an older file for a moving ref gets that file, with no error, when the `HEAD` fails.** The fixture did not advance `main`, so this shows the fallback exists, not how stale its result can be.

**Resume.** Both versions resume, in different ways. Against Athena, a proxy cut SDK 2.1.1's first `GET` partway through; the same call sent a `Range` request, received 206 and finished with the correct SHA-256. Separately, the correct first 1 MiB of the file was placed by hand as a leftover `.incomplete` file: SDK 1.8.0 [completed from it](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py#L1812-L1813), while SDK 2.1.1 ignored it and downloaded the whole file, because it [writes each call to a new temporary file](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py#L1995-L2003). The seeded check is not a recovery from a real interruption across calls.

## Fetching on a cold request

A gateway outside the package wrapped the server: a request for a file not held started a worker running the existing acquisition code, and answered 503 with `Retry-After: 2` until the verified file was published. It ran on loopback against a fake Hub with SDK 2.1.1 and a 720,896-byte file. Slow acquisition was simulated by a sleep after the worker's download finished, so none of this measures a large transfer on a real link.

Content-keyed coordination worked: six concurrent cold clients across two repositories produced one upstream payload `GET` and one stored file, a renamed copy in a third repository needed none, and the repository's other quantization was never fetched. A corrupt same-size upstream payload was rejected and no payload blob was published.

What did not work, or was not covered:

- **An immediate 503 on `HEAD` covered only short delays.** With a simulated 30-second acquisition the first call failed at about 15 seconds and the next succeeded. Holding each `HEAD` for up to 8 seconds before the 503 made that case succeed on the first call. Nothing longer was tried.
- **Holding the `GET` instead failed** for a 12-second delay, because the `GET` 503 is not retried.
- **Metadata can appear before bytes.** A failed or stopped acquisition still published the revision's tree, and a ref the archive did not have before, with no payload behind them.
- **Unseen repositories answered 404 on API routes.** Only `resolve` triggered acquisition, so a cold `snapshot_download`, which lists files first, was not covered.
- **Only a graceful stop was tested.** The gateway was stopped with `SIGTERM`, not killed or cut off mid-transfer.

## Limits

- Scale was one 4.1 GB shard, one 17 MB model and synthetic files.
- **Startup readiness.** Re-verifying that shard at startup took about 8.6 seconds, and the cost for a whole library is unknown. Until a file is verified, clients see the 503 behavior in the table above: retries run out, or an older cached file is returned. A finished prefetch is not a ready server.
- **A 50 GB ceiling on plain HTTP files, from source only.** `huggingface_hub` 1.8.0 and 2.1.1 set `MAX_HTTP_DOWNLOAD_SIZE` to 50,000,000,000 bytes ([1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/constants.py), [2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/constants.py)), and a fresh plain HTTP download of one file known to be larger raises an error suggesting `hf_xet` ([1.8.0](https://github.com/huggingface/huggingface_hub/blob/v1.8.0/src/huggingface_hub/file_download.py), [2.1.1](https://github.com/huggingface/huggingface_hub/blob/v2.1.1/src/huggingface_hub/file_download.py)). No file that large was tested or is known to be needed here, and smaller shards are unaffected whatever the model's total size. It is a constraint on any server that speaks only plain HTTP.
- Clients were `huggingface_hub` 1.8.0 and 2.1.1 only, with no llama.cpp or inference application, and never over the LAN directly.
- A stream that stalls without closing, removal of content, access control, gated content and power loss were not tested.
