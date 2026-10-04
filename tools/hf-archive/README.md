# hf-archive

A verified, selective archive of Hugging Face model files, served read-only to stock `huggingface_hub` clients through `HF_ENDPOINT`. The name is descriptive; the product name is undecided. This is a bounded first slice for qualification and is not deployed anywhere: nothing under `synology/` references it.

Two programs share one archive directory. Acquisition commands (`pull`, `import`) talk to the Hub or read local files, verify every byte, and publish. The server (`serve`) only reads the archive: it has no HTTP client, holds no token, forwards nothing upstream, and exposes no write route. Once a revision is archived, clients keep working with the upstream unreachable and across server restarts.

## Commands

Every command takes the archive root from `--root` or `HF_ARCHIVE_ROOT`. `pull`, `import`, `list` and `verify` accept `--json` to print a result document on stdout; diagnostics go to stderr.

```sh
# Acquire selected files of a revision. Selection is mandatory: --include globs, or --all.
hf-archive pull ORG/NAME --root /archive --include '*.json' --include '*Q4_K_M.gguf' [--exclude GLOB] [--revision main]
hf-archive pull ORG/NAME --root /archive --all --revision 6f75de8b60a9f8a2fdf7b69cbd86d9e64bcb3837

# Import files already on disk. Metadata comes from the Hub, or from a document with --metadata (no network).
hf-archive import ORG/NAME --root /archive --source ~/.cache/huggingface/hub/models--ORG--NAME [--revision main]
hf-archive import ORG/NAME --root /archive --source /models/dir --metadata meta.json --map model.safetensors=renamed-local.safetensors
hf-archive import ORG/NAME --root /archive --source /models/dir --metadata meta.json --move-refs   # also replace recorded refs

# Print the metadata document of a revision, for a later offline import.
hf-archive metadata ORG/NAME --revision main > meta.json

# Serve. Read-only; safe to run on a read-only mount of the archive.
hf-archive serve --root /archive --host 0.0.0.0 --port 8080

hf-archive list --root /archive      # repositories, refs, revisions, acquired/total files
hf-archive verify --root /archive    # re-hash every referenced blob
```

Shared acquisition options: `--revision` (branch, tag, full ref such as `refs/pr/3`, or commit; default `main`), `--endpoint` (default `https://huggingface.co`; `HF_ENDPOINT` is deliberately ignored because on an archive client it points at the archive), `--token-file` or `HF_TOKEN`, `--include`/`--exclude` (same glob semantics as `snapshot_download` patterns), and `--reserve-bytes` (free space to keep, default 1 GiB).

Exit codes: `0` success, `1` failure, `2` usage, `3` integrity mismatch (also `verify` finding problems), `4` repository out of scope, `5` not enough space.

Clients need only `HF_ENDPOINT=http://host:8080` and use original repository IDs:

```python
snapshot_download("ORG/NAME", allow_patterns=["*.json", "*Q4_K_M.gguf"])
hf_hub_download("ORG/NAME", "config.json", revision="6f75de8b60a9f8a2fdf7b69cbd86d9e64bcb3837")
```

## Scope and limits

- Models only. Datasets and Spaces answer as unknown repositories.
- Public, ungated repositories only. Acquisition refuses a repository unless upstream metadata says `private: false` and `gated: false`; unknown counts as refused. The server has no per-client access control, so anything in the archive is readable by everyone who can reach it. A token only raises rate limits; use a read-only one. A locally stored HF token is never picked up implicitly.
- No automatic cold fetch. A client asking for a file that was not acquired gets an error naming the `pull` command that would prefetch it. Nothing is downloaded in response to a client.
- No garbage collection and no removal command. Blobs and revisions stay until deleted by hand.
- Files are acquired one at a time. A single file above 50 GB cannot be fetched by stock clients over plain HTTP, which is an SDK limit.
- The archive must live on one filesystem that supports `flock`, atomic rename and `fsync`.

## What clients see

The server implements the routes `huggingface_hub` 2.1.1 uses for downloads: model info (`/api/models/{repo}` and `/revision/{rev}`), `/refs`, `/tree/{rev}` (recursive or not, single page), `POST /paths-info/{rev}`, and `HEAD`/`GET /{repo}/resolve/{rev}/{path}`, plus `/healthz`. Any other route is 404 and any other method is 405.

`resolve` answers directly, without a redirect, with the headers upstream reports: `ETag` is the LFS SHA-256 for LFS files and the Git blob id otherwise, with `X-Repo-Commit` and the exact `Content-Length`. `GET` honours a single `Range` (and `If-Range`) so interrupted client downloads resume. No response carries a Xet hash or Xet header, in file headers, tree listings, or path info; a client that saw one would bypass the archive and fetch from upstream.

Tree listings and model info always describe the complete upstream tree of the commit, including files the archive does not hold. That keeps the metadata honest, and it decides how `snapshot_download` behaves:

- With `allow_patterns` matching only acquired files, or with `hf_hub_download` of an acquired file, it succeeds.
- Without patterns on a partially acquired revision it fails on the first file that is not acquired, rather than returning a snapshot that silently lacks files.

Errors use the Hub's `X-Error-Code` convention so the SDK raises its usual exceptions:

| Situation | Status and code | SDK behaviour |
| --- | --- | --- |
| Repository not archived | 404 `RepoNotFound` | `RepositoryNotFoundError` |
| Revision not archived, or ref never recorded | 404 `RevisionNotFound` | `RevisionNotFoundError` |
| Path is not in the commit's tree | 404 `EntryNotFound` | `EntryNotFoundError`, cached by the client as absent |
| Path exists upstream but is not acquired | 404 `EntryNotAcquired` | generic `HfHubHTTPError`; deliberately not `EntryNotFound`, so clients do not cache it as absent and succeed after a later `pull` |
| Blob missing, unreadable or failing verification | 500 `ArchiveInconsistent` | request fails; nothing is served |
| Blob whose verification has not finished in this server process | 503 `VerificationPending` with `Retry-After` | retried by the SDK; see below |

Refs are the last ones an acquisition observed upstream. A `pull`, or an `import` that fetches its metadata from the Hub, records where the requested branch or tag pointed and moves a ref already recorded elsewhere. The server never refreshes refs, so `main` keeps resolving to the last archived commit until an acquisition moves it. Commit-pinned requests work for every archived commit.

An `import --metadata` works from a saved document of unknown age, so it does not move recorded refs by default. It creates the refs the document lists that the archive does not have yet, and leaves any ref already recorded at another commit where it is. The imported commit is still published and reachable by commit hash. The archive does not try to work out which commit is newer: commit order is not derivable from the documents it holds. `--move-refs` replaces recorded refs with the ones the document lists, which is how to point a ref at an older commit on purpose. The result says what happened: `refs` lists the refs that name the imported commit afterwards, and `refs_retained` maps each ref left alone to the commit it still names; the text summary prints one line per retained ref.

### Verification at serve time

The server hashes a blob before first serving it in each process, and again if the file on disk changes, checking both that the bytes match the blob's name and that they carry the identity the manifest records for the file. A blob corrupted or swapped after publication is refused with 500 on `HEAD`, `GET` and ranges alike.

Hashing runs in the background. A request for a blob whose hash is not yet known waits up to 2 seconds, which covers small files, and otherwise gets 503 `VerificationPending` with `Retry-After` rather than hanging. Unverified bytes are never sent.

A hash that fails is a result as well. A blob that cannot be opened, or whose read fails part-way (an I/O error, for example), is refused with 500 `ArchiveInconsistent`, not 503. When the failure happens in the background hash, the server remembers it and does not hash the blob again while the file on disk is unchanged. It tries again when the file's inode, size or timestamps change, so a blob repaired by replacing the file recovers without a restart; a background failure that clears without changing the file needs a restart of `serve`, which starts with no remembered results. When the request itself cannot open the blob (a permission error, for example), nothing is remembered: each request tries the open again, and the blob is served as soon as the open succeeds. 503 is reserved for a file with no result yet: its hash is still running, or the file was replaced while it was being hashed and the next request starts on the new one.

`serve` runs a verification pass over every referenced blob at startup and repeats it every 30 seconds, so content acquired while the server runs is picked up without a restart. `/healthz` answers 503 with `"status": "warming"` while a pass is hashing something, and 200 with `"warm": "ready"` and a `warm_failures` count otherwise. `warm_failures` is the number of file references the last pass found unservable (missing, unreadable, or failing verification), and a failed reference that has not changed is counted again on every later pass. A 200 therefore means the pass finished, not that everything is servable: a readiness check has to require `"warm_failures": 0` as well. Health is a statement about the last pass, not about content published since: for up to one interval plus hashing time after an acquisition, new blobs can still answer 503.

What a client does with that 503 is the SDK's choice, and it matters for moving refs. A client with nothing cached retries, and succeeds if hashing finishes inside the SDK's retry window or fails with an error if it does not. A client that already has an older commit of the same ref cached falls back to that older copy without an error, exactly as it does when huggingface.co is down. A 200 from `/healthz` right after an acquisition may still describe the pass before it. The reliable sequence after acquiring a new revision is to restart `serve` and wait for `/healthz` to return 200 with `"warm": "ready"` and `"warm_failures": 0` before pointing clients at it. Pinning a commit avoids the silent fallback but does not make the blob ready sooner. A full pass costs one sequential read of the archive per server start.

## Storage

```text
archive.json                                   {"format": "hf-archive", "format_version": 1}
blobs/sha256/<aa>/<sha256>                     verified content, ordinary read-only files
aliases/git-sha1/<aa>/<sha1>.json              Git blob id -> sha256 lookup hint
aliases/xet/<aa>/<hash>.json                   Xet hash -> sha256 as asserted by upstream; never used for lookup
repos/models--ORG--NAME/revisions/<commit>.json
repos/models--ORG--NAME/refs/<url-quoted ref>.json
staging/  locks/  quarantine/
```

Content is stored once by SHA-256, across repositories, revisions and renames. Blobs are plain files: a blob can be read, hashed or copied with ordinary tools.

Three identities are kept apart. The LFS SHA-256 is the content's SHA-256. The Git blob id identifies the content of a regular Git file, and for an LFS file identifies only the pointer, so it is never compared with LFS content. The Xet hash is recorded as upstream's assertion and is never compared with either.

A revision manifest is self-describing JSON with `"schema": "hf-archive.revision"` and `"schema_version": 1`. It holds the repository ID, commit, visibility, a small set of upstream model-info fields, the directories, and every file of the commit's tree:

```json
"model.safetensors": {
  "size": 17756393,
  "git_oid": "<pointer oid>",
  "lfs": {"sha256": "<sha256>", "pointer_size": 133},
  "xet_hash": "<hash>",
  "blob": "<sha256>",
  "acquired": {"via": "import", "at": "2026-10-03T12:00:00Z", "source": "/path/as/given", "resolved": "/real/path"}
}
```

`"blob": null` means not acquired. `via` is `pull`, `import`, or `reuse` when the content was already in the store. A `history` list records each acquisition (time, action, tool version, source, paths). A ref document (`"schema": "hf-archive.ref"`) holds the full ref name, the commit, and the commits it previously pointed at.

The metadata document accepted by `import --metadata` and printed by `metadata` is `{"schema": "hf-archive.metadata", "schema_version": 1, "info": {...}, "tree": [...], "refs": [...]}`, where `info` is Hub model-info JSON (`id`, `sha`, `private` and `gated` are required), `tree` is the Hub's recursive tree listing (`type`, `path`, `oid`, `size`, and for LFS files `lfs: {oid, size, pointerSize}`), and `refs` lists full ref names such as `refs/heads/main` that pointed at this commit when the document was made.

## Acquisition guarantees

Every acquired file passes through the same sequence: staged in `staging/` on the archive filesystem, read back and checked for exact size plus LFS SHA-256 (LFS files) or Git blob id (regular files), made read-only, fsynced and renamed into `blobs/`. Only after every selected file is in place is the manifest written, and only after that are refs created or moved. New directories are persisted in their parents.

Publication is atomic per document, not one transaction across them. Each blob, manifest and ref appears through its own durable rename, in that order, so what a reader can see is always valid, but an acquisition can stop between steps:

- A mismatch, download failure or full disk while acquiring payloads changes no manifest and no ref. The previous revision stays valid and served. Blobs verified before the failure stay in the store and a retry reuses them.
- A failure or kill after the manifest is renamed but before a ref moves leaves the new revision published and reachable by commit, with the ref still on the previous commit. A failure after a rename but during the directory fsync can report an error for a document that is in fact published. In both cases the command exits non-zero and re-running it converges; clients see valid content throughout.
- A manifest is never written while any blob it references is absent or has the wrong size.
- Free space is checked against the selection before any payload moves. Running out mid-way is reported as exit 5.
- Existing content is reused only after it is re-read and proven to match the identity upstream declares for the wanted file. The blob's name and the alias files are hints, not evidence. A stored blob whose bytes do not hash to its name is moved to `quarantine/` and acquired again.
- Acquisitions of the same expected content are serialised by a content-keyed lock, across processes and repositories, so concurrent pulls of one weight download it once. Checking, quarantining and replacing a stored blob is serialised per blob. Manifest and ref updates are serialised by one publish lock, and the decision to keep or move a recorded ref is made under it. Readers take no locks.
- Staging directories of a process that died are removed by the next acquisition.
- The SDK's own caches (`HF_HOME`, hub cache, Xet cache) are redirected into a staging directory that is deleted when the command ends, with the Xet chunk cache disabled unless `HF_XET_CHUNK_CACHE_SIZE_BYTES` is set. The archive is the only retained copy.

Imports copy and never link, so the source stays intact and later edits to it cannot reach the archive. `--source` may be a plain directory, a native HF snapshot directory, or a native HF repo cache directory containing `snapshots/<commit>`; symlinks under it are followed. Tree files with no source file stay not acquired, so a directory holding only one weight publishes a revision with that one file. `--map` names the source path of a file stored under a different name; a mapped source that is missing is an error. A source file whose size or hash differs from upstream fails the whole import. Repository paths from metadata and manifests are validated before use, and writes are confined to the archive root.

The server ignores and removes `HF_TOKEN` from its own environment at startup. Results of running this slice against real models and the NAS are in [QUALIFICATION.md](QUALIFICATION.md).

## Development

```sh
uv sync --locked
uv run pytest
uv run ruff check
```

Core tests need no network. `huggingface_hub` is pinned to 2.1.1 in `pyproject.toml` and `uv.lock`; the server's wire behaviour was written against that release's download and metadata code paths.

## Container

`Dockerfile` builds one image for all commands. `compose.qualification.yaml` is a standalone stack for qualification, kept out of the `synology/` tree that deploys from `main`: a `serve` service with the archive mounted read-only, and an `acquire` service to run on demand.

```sh
docker compose -f compose.qualification.yaml build
docker compose -f compose.qualification.yaml run --rm acquire pull ORG/NAME --all
docker compose -f compose.qualification.yaml up -d serve
```
