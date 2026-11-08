# SparkNest on Athena

SparkNest v0.8.3 runs as one container and one voting node on Athena, using TCP and an internal FUSE mount. This is an evaluation deployment, manually applied from this directory; it is not in `synology/.doco-cd.yml`. Bulk qualification is tracked in [#1371](https://github.com/kelchm/home-lab/issues/1371). Original NAS stores remain independent. The owner authorizes removing captured Mac model copies after their selected backup and recovery checks pass, following the confirmed local model cleanup procedure below. Retain active inputs, changed/new files, unique training outputs and any source whose checks fail.

The UI/API is at `http://10.32.10.5:8091/` on Main and `http://10.32.25.5:8091/` on Storage. There is no management-VLAN listener. The UI shell is public; API requests require the cluster-derived bearer token. Retrieve the login link on Athena with `sudo /usr/local/bin/docker exec sparknest-sparknest-1 nest ui`, replacing its container hostname and port with `10.32.10.5:8091`. Do not commit that link or the token. Traffic is HTTP on these LAN addresses.

## Storage

| Athena path | Container path and purpose |
| --- | --- |
| `/volume1/docker/sparknest` | `/config`, read-only configuration, agent script and expected-hash manifests |
| `/volume1/docker/sparknest/cluster.secret` | `/config/cluster.secret`, locally generated secret, mode 0600 |
| `/volume1/models/sparknest/state` | `/data/state`, SQLite/Raft metadata and payload objects |
| `/volume1/models/sparknest/incoming` | `/data/incoming`, disposable staging links and retained verified payload receipts |
| `/volume1/models/sparknest/recovery` | `/data/recovery`, local archives and recovery exports |

All three data directories use **one parent bind**, `/volume1/models/sparknest:/data`. DSM's 4.4 kernel returns `EXDEV` for hardlinks and FICLONE across separate bind mounts even when they refer to the same Btrfs volume. One bind allows native import to adopt staging copies without copying their payload again. Athena sources are first cloned into independent staging inodes with FICLONE, with a copying fallback; originals are never hardlinked or mounted into the container. Staging and recovery consume Athena capacity and share its failure domain.

The organized namespace exists at `/mnt/sparknest` inside the container. HF repositories are under `/hf-home/hub`; unversioned local stores are under `/imports/<source>/<store>/<collection>`. The namespace is not exposed as SMB/NFS or a host mount. ID-named files under `state` need their matching metadata to reconstruct meaningful paths. FUSE creates the organized view of those files; it is not a separate backing disk.

The container runs as root with only `SYS_ADMIN` added after dropping all capabilities, `/dev/fuse` exposed, and AppArmor unconfined to permit mounting. It has no Docker socket. Its root filesystem and configuration bind are read-only; the whole `/data` bind is writable. Staged files use 0444 and directories 0555, but this is not an isolation boundary around every receipt or provenance file. Expected-hash manifests are written by the host agent under the read-only configuration bind. Memory is limited to 2 GiB; CPU shares are a relative scheduling weight. Athena rejects CPU quotas and discards PID limits, so neither is configured. FUSE negotiates ABI 7.23 with no passthrough; RDMA and FUSE io_uring are disabled.

## Image and deployment

`Dockerfile` packages the unchanged upstream Linux amd64 v0.8.3 release, checks its SHA-256, and adds FUSE, libibverbs, Hugging Face Hub 2.4.0, and hf-xet 1.8.0. The release commit is `7c512a8a9d8e53103176a8b7e98f7b98b8ff62d1`. The Compose file pins the image actually tested on Athena. GHCR is private; a fresh pull requires package read access. Initial build/pull credentials were temporary and removed afterwards.

```sh
docker build --platform linux/amd64 --provenance=false -t ghcr.io/kelchm/home-lab/sparknest:<unique-tag> synology/sparknest
docker push ghcr.io/kelchm/home-lab/sparknest:<unique-tag>
```

Update the Compose digest after publishing a replacement. On Athena, create the directories in the table, restrict state and recovery to root, and generate a random 32-byte hex cluster secret in the secret file. Copy `compose.yaml`, `node.toml`, and `ingest.py` into `/volume1/docker/sparknest/`. Pull the pinned image before starting.

Bootstrap exactly once, only with an empty state directory. Do not put `--bootstrap` in the service command or repeat it on an existing deployment:

```sh
cd /volume1/docker
sudo test -z "$(sudo ls -A /volume1/models/sparknest/state)"
sudo /usr/local/bin/docker compose -f sparknest/compose.yaml run -d --no-deps --name sparknest-bootstrap sparknest sparknestd --config /config/node.toml --bootstrap </dev/null
sudo /usr/local/bin/docker exec sparknest-bootstrap nest wait-ready --timeout 30
sudo /usr/local/bin/docker stop --signal SIGINT --time 45 sparknest-bootstrap
sudo /usr/local/bin/docker rm sparknest-bootstrap
sudo /usr/local/bin/docker compose -f sparknest/compose.yaml up -d </dev/null
sudo /usr/local/bin/docker exec sparknest-sparknest-1 nest wait-ready --timeout 30
```

For later applies, update the applied Compose/config copies and use only `compose up -d`. Use `docker exec` without `-i` unless input is intended; Compose's interactive commands can consume later commands from an SSH script's standard input. Pause the coordinator before replacing its local or NAS script, and replace the NAS script atomically. There is no need to restart the daemon for an ingest-script update.

## Bulk ingestion

`ingest.py` is a stdlib-only Mac coordinator and Athena agent, compatible with Athena's Python 3.8. It never deletes or changes an original. The current runtime directory on the Mac is `/Users/kelchm/Documents/sparknest-ingest-20261108/`:

| Runtime file | Purpose |
| --- | --- |
| `sources.json` | Explicit hosts and source roots; Spark/Athena reads use sudo |
| `inventory.json` | Captured source paths, stat identities, available hashes, revisions and refs |
| `ingest.py` | Frozen copy of the version used for this run |
| `journal.json` | Completed files, per-collection progress, native job details and verification |
| `worker.log`, `worker.json` | Worker output and process identity |
| `progress.py` | Read-only live progress display for the coordinator |
| `ssh/` | Private persistent SSH control sockets, no private keys |

The inventory covers Athena's ModelKeep archive and import-staging caches; the Mac's HF, LM Studio, Ollama, ComfyUI and flatrate adapter/checkpoint stores; and both Sparks' shared/home HF caches, runtime inputs and existing OCR models. On November 8 it contained 98 collections, 4,888 files and 5,845,171,861,725 logical bytes, including duplicated stores and revisions. Five optional source roots were absent on the Sparks and are reported in inventory warnings. GPU `ComputeCache` directories, `.locks`, zero-byte lock files, Synology metadata, and HF authentication files are excluded. These are configured model stores, not a scan of every disk. Missing/unreadable roots, traversal failures and linked directories are reported; linked directories inside walked trees are not followed.

A source specification is an array of `{name, host, sudo, stores}`. `host` is `local` or an SSH destination. Stores have `{name, root, kind}`, where `kind` is `hf`, `modelkeep`, `models` (owner/model folders), or `tree`. A tree store can specify `exclude_dirs`, such as `["ComputeCache"]`. Refresh the inventory when sources change; the captured stat identity prevents silently ingesting changed files under an old inventory.

```sh
RUNTIME=/Users/kelchm/Documents/sparknest-ingest-20261108
python3 "$RUNTIME/ingest.py" inventory "$RUNTIME/sources.json" "$RUNTIME/inventory.json" </dev/null
```

For a detached run, first open persistent SSH connections in the foreground while the SSH agent is reachable. The worker then reuses the connections without requiring the tool session's SSH agent. Keep the socket directory private and the Mac awake: remote payloads travel through the Mac to Athena.

```sh
mkdir -p "$RUNTIME/ssh"
chmod 700 "$RUNTIME/ssh"
for ip in 10.32.20.5 10.32.21.31 10.32.21.32; do
  ssh -n -M -S "$RUNTIME/ssh/$ip" -o ControlPersist=86400 -o BatchMode=yes \
    -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -f "kelchm@$ip" true
done
export SPARKNEST_SSH_CONTROL_DIR="$RUNTIME/ssh"
caffeinate -i python3 -u "$RUNTIME/ingest.py" run "$RUNTIME/inventory.json" "$RUNTIME/journal.json" </dev/null
```

Run the last command in a retained terminal, or detach it with output to `worker.log` and record its process group. `journal.json.lock` prevents concurrent coordinators for that journal. An SSH transport failure stops the queue rather than failing every remaining collection. Restore connections and rerun the same command to resume. Completed receipts survive interruption; partial files restart from the beginning. Previously completed staging paths are checked on resume and missing ones are discarded from the reuse index. **Do not clean `incoming/bulk` while a run or its journal still depends on it.** No automated cleanup or source deletion is included.

Monitor the whole queue from the Mac with the following command. Copy `progress.py` into the runtime directory first when setting up another run. Add `--once` for a single report. Ctrl-C stops only the monitor.

```sh
python3 /Users/kelchm/Documents/sparknest-ingest-20261108/progress.py
```

The display shows completed collections, logical bytes verified, the current source and phase, per-file staging boundaries, failures, and repos with native sharing skips. The [Jobs page](http://10.32.10.5:8091/#/jobs) covers only native imports and sharing, so all jobs can say done while the coordinator clones or hashes another collection. Hashing a large file can take several minutes between journal updates; the monitor does not invent byte progress within a hash operation.

The November 8 coordinator was restarted at 14:48 UTC with matching coordinator/NAS agents, direct Spark transfers and the native LM Studio inventory. Its captured script SHA-256 is `87cd1b264105694bc98919619f1f0e6920aec66fafd6956955f7ba7b5c31d7a1`; `worker.json` records the active PID, hash and transport. Previous scripts, inventory and journal are preserved in the runtime directory. Reclassification assigns new keys to LM Studio collections, so earlier generic verification counts do not count as native-model acceptance. Inventory fixes for HF tree identity, linked directories and ref verification are included in this launch.

The loader verifies SSH streams with source/destination SHA-256, and validates recorded ModelKeep/HF/Ollama checksums when available. It can reuse a verified Athena copy by checksum instead of reading a duplicate on another machine. After native import it reads every expected archived file through FUSE and compares its full SHA-256. `verified` means the archive holds the expected content; it does not prove that a reused source's own bytes were read, that a partial HF cache is a complete upstream model, or that an independent backup exists.

HF models retain their repo names, actual commit IDs, original filenames, tree metadata when present, and source-specific ref aliases under `refs/sources/...`. Shared branch refs are added only when the full inventory agrees on the target. Conflicting `main` refs exist for DFlash2 and GLM-5.3-Flash-EXL3-TR3; pre-existing archive refs are retained, and the source aliases identify each source's target. LM Studio folders require verified native HF identities; the loader refuses a generic fallback for that store. Other local folders without an authoritative HF revision remain folders under `/imports`; they are not assigned invented HF commits or presented as fully cataloged HF models.

Original HF blob names are borrowed from other copies of the same repo/revision/file when available. Otherwise the loader computes a SHA-256 name. For LFS weights this recovers the normal HF blob identity; Git-managed metadata can use another naming convention. Native `hf share` requires HF tree/Xet metadata to publish eligible weights into the global shared store; Git-managed files stay in their repositories. It can skip unknown files even when the job succeeds. The journal retains its notes and skips: full content verification does not imply that every file deduplicated. This is whole-file sharing, not chunk deduplication.

**Observed metadata dependency:** `wrldsuksgo2mars/DeepSeek-V4-Flash-Vision-Exp-EXL3-K2.2-D2-v1` at `8347bfb8776287ef2dcab2b46e9f15c655825c3a` was retained and passed full content verification, but `hf share` skipped all 31 files, including all 11 weight shards (91,839,042,912 bytes). Both inventoried Athena copies lack HF tree/Xet records; ModelKeep retained only path, size and SHA-256. Anonymous Hub requests returned 401; a validated fine-grained token returned 404 for both the repo and revision, including through the official SDK inside Athena's container. The cause of that unavailability is not established, and a fine-grained token does not establish the account's full access. The correct SHA-256 weight names alone are insufficient for the native shared-store conversion. Repeated imports into that same repo/revision can still reuse its existing blobs; this is not evidence of cross-repository deduplication. The observed repo has 31 ordinary blob files totaling 91,886,448,032 bytes and no shared-store links. This remains a native archive limitation in #1371, independently of successful byte verification.

Published metadata from the original source revisions matches 39 archived LFS files (105,925,426,626 bytes) from the Mia Qwen mirror and 123 (175,715,659,341 bytes) from the Mia GLM mirror by SHA-256. The sources are `local-inference-lab/Qwen3.8-Flash-Next-NVFP4@ada4da32a583a78aa47299f45a70603c950490b8` and `brandonmusic/GLM-5.3-Flash-tr3-4bpw@5ab363a8dcf6405955fd5f99671e01a1c9fb124b`. Those records are saved in the Mac runtime directory; they have not been applied to the archived mirrors. The experimental local-Xet-ID helper patch is also undeployed.

### Catalog and appearance findings

The November 8 UI review used the deployed v0.8.3 page and the source at release commit `7c512a8a9d8e53103176a8b7e98f7b98b8ff62d1`. These are evaluation findings and candidate upstream changes, not implemented UI features. Native model recognition for LM Studio is now an ingest acceptance requirement; the other UI requests remain separate improvements.

- **Generic imports are permanent namespace entries.** `/imports/mac/lmstudio/<owner>/<model>` and `/imports/mac/comfyui/...` are the loader's final, source-preserving destinations, distinct from `/data/incoming/bulk` staging. SparkNest's model-shaped space view recognizes HF repos under its configured hub; all remaining files fall under `other files` in directory shape. Finishing the queue or renaming these folders does not make them cataloged models. Native recognition of loose model folders and Ollama manifests remains a fit gap. LM Studio collections must instead pass the pinned HF conversion below or be excluded.
- **The treemap hides useful model boundaries.** It draws three levels at a time; `other files` → `imports/mac` → `lmstudio` consumes those levels before reaching individual models. Clicking a group zooms in. A bounded presentation change could expose individual model collections while keeping source paths available as provenance. Long `sources/...` revision labels come from our loader's preservation refs; primary branch/short-commit labels with expandable aliases would keep that provenance without putting every alias in the headline.
- **Rows represent revisions, despite the “repos” label.** The live DFlash2 catalog has one repo with two commits: `dc77ff1c99eeb2df044ee3d4f0094eb033fee410` under `main` and `bf582e4eacc1810f76656d1811693ff6c6737d2a` under source refs. The API already groups aliases that point to one commit, but emits a separate row for each distinct referenced revision. A single model row with expandable revisions and secondary source details would use the available metadata more clearly; duplicate-looking rows do not establish duplicate physical payloads.
- **Detached revisions are poorly explained.** At 14:45 UTC the SDK reported four real commits with no refs: TensorFold `4996441a82d9…` (Spark caches had no refs), Qwen3.5-4B-Base `1001bb4d826a…` and kev-4b `6cfce5c2fa4b…` (Mac caches had no refs), and Mia GLM `25a44fdbf168…` (older than `main` in the copies ingested so far; one queued source still references it). The fifth entry was an 82-byte Synology `@eaDir` snapshot artifact imported from Athena's staged hub: the inventory skips that name inside file walks but missed it at the snapshot-directory level. No prune was applied. The model detail page shows only a short commit badge with no ref arrow for a detached revision; it has no explicit detached or retention label. The tidy dialog and CLI dry run show only aggregate counts, despite the dialog suggesting the CLI lists entries. A bounded UI change should list affected repos/commits and unique bytes before pruning, distinguish retained history from disposable cache, and make ref/provenance state visible on each revision. Source filtering and archive-retention refs need separate loader follow-up; unreferenced history is not a failed integrity check.
- **A simpler appearance is requested.** Keep the existing visual style as an option and add a plain option without the animated background, glow and decorative motion. The background currently always creates its canvas; reduced-motion preference gives it a still frame. A background-off option should avoid starting its WebGL renderer, with the preference persisted per browser.
- **Light mode is not implemented in v0.8.3.** Both the HTML metadata and root stylesheet force dark color scheme. The live page stayed dark while the browser reported a light system preference; no theme selector or light palette was found. A bounded theme change should offer System/Light/Dark, persist overrides, follow system changes when selected, and cover component and canvas colors as well as the base stylesheet. Keep theme choice independent of decorative effects.
- **Model browsing needs metadata and filters.** The current list searches repo/ref text, filters model versus dataset, and sorts by size, recent use or name. First expose existing fields such as author, revision, size, location and local completeness through useful columns and filters. Separately extract locally available model-card/config/header metadata for architecture, format, parameter count, dtype/quantization, task, tags and license where those fields are actually recorded; keep unknown values explicit and retain provenance. Metadata extraction must remain usable offline.
- **Tokenizer-only caches look like complete models.** `gpt2` is a valid legacy Hub alias; the API redirects it to `openai-community/gpt2` at `607a30d783dfa663caf39e06633721c8d4cfcd7e`. Spark 1's user cache contributed only `tokenizer.json` (1,355,256 bytes). The three-file UI count includes `main` and the importer source ref. The captured source file passed full FUSE verification; no weights were lost by ingestion. Green/“complete” describes the held namespace files, whereas Finish compares against the upstream repository. Neither identifies a runnable model or distinguishes an intentional subset of quantizations from an interrupted download. Show cached asset types/weight presence, local selection coverage and upstream availability separately, retain legacy aliases for clients, and show failed Hub lookups as unknown rather than zero missing. Do not automatically fetch a repo's remaining formats/quantizations to make its badge green.
- **Add upstream links for known identities.** Repo, pinned revision and file links to Hugging Face are a small independent UI change. Do not invent upstream identities for loose imported folders or imply that an upstream link is currently reachable.

### LM Studio native conversion

`lmstudio.py` converts the existing LM Studio inventory into standard HF repo/snapshot/blob imports using saved Hub API trees. Each metadata record is named `<original-item-key>.json` and contains the canonical `repo`, immutable 40-character `revision`, and the API tree `files`. Fetch the tree at the pinned revision with `GET https://huggingface.co/api/models/<repo>/tree/<commit>?recursive=true&expand=true`, following pagination. If current files do not match, inspect actual repository history; an LM Studio directory name alone is insufficient evidence. Save the actual responses outside Git in the runtime directory. No token is written into this metadata.

The converter checks every local path and size against the published tree, compares existing receipt hashes, and reads small Git-managed metadata to recover its Git blob ID. Renames require a unique content match. Large files without receipts remain candidates until the receiver verifies their published SHA-256. A missing or conflicting mapping stops conversion; no invented commit or generic fallback is accepted. A selected subset of quantizations is recorded as a partial snapshot. Original local filenames and collection identity remain in the provenance record.

Stop the coordinator and preserve its inventory and journal before running the converter:

```sh
python3 lmstudio.py --inventory "$runtime/inventory.json" --journal "$runtime/journal.json" --metadata-dir "$runtime/lmstudio-native" --output "$runtime/inventory-native.json"
```

The output uses new collection keys so earlier generic `verified` states cannot bypass native conversion. The companion `.proofs.json` contains Git hashes tied to SHA-256 for earlier receipts; merge only proofs whose SHA-256 agrees with the receipt, under the stopped coordinator's journal lock. Install the replacement inventory and matching agent/coordinator together. Retain the original inventory, old journal states and payload receipts for audit and resume.

After import, native sharing and full FUSE content verification, the loader checks that the repo appears in SparkNest's model catalog. Only then does it remove the old `/imports/mac/lmstudio/<owner>/<model>` namespace entry with `nest rm`; source files and staging receipts remain untouched. A sharing skip or missing catalog entry blocks cleanup. This fixes the representation for collections with real HF identities; it does not add a native loose-model catalog to SparkNest. The first live qualification converted the real 1,631,205,312-byte Muse Glimmer drafter into `meta-models/Muse-Glimmer-30B-GGUF@43c7eadd41352a299ea8e0a36b3157978dd63596`: one blob entered the native shared store, all three expected paths passed FUSE SHA-256 verification, the native catalog contained the repo, and the old generic entry was removed. Retrying cleanup with that path absent succeeded. All 17 collections have pinned candidate mappings; remaining large files still require live checksum and catalog acceptance.

### Direct transfers

The coordinator defaults to Athena's main-VLAN 10 GbE address, `10.32.10.5`, and verifies its host key using the already pinned management-address identity. With `SPARKNEST_DIRECT_SOURCES=1`, Spark payloads are pulled by Athena directly from the Sparks' `10.32.25.31` and `10.32.25.32` storage-VLAN addresses; only control messages and receipts cross the Mac. There is no fallback to a Wi-Fi relay when direct transfer fails. The client explicitly selects the pinned Ed25519 host-key algorithm because DSM's older SSH client otherwise selected ECDSA despite the supplied Ed25519 pin. Mac-only content still makes one Wi-Fi hop to Athena.

The Athena agent reads `/volume1/docker/sparknest-transfer/sources.json`, a root-only mapping from inventoried source host to SSH host, identity-file and known-hosts-file paths. Pin the Spark public host keys obtained over existing trusted SSH sessions. Use a dedicated temporary key kept only on Athena, with its public key installed on each Spark as `from="10.32.25.5",restrict,command="python3 <agent> send-authorized <allowlist>"`. The allowlist maps each inventoried resolved source path to its recorded stat identities. The forced command accepts only an unchanged inventoried file and retains the original streaming SHA-256 protocol. It grants no shell, agent forwarding or port forwarding. Keep private keys outside the container's binds. All 1,130 unique inventoried source paths on Spark 1 and 840 on Spark 2 were successfully opened read-only as `kelchm` before deployment; the forced source command needs no sudo. After the bulk run, remove these marked authorized-key entries and the dedicated transfer credentials/scripts; leave other SSH configuration alone.

Completed receipt copies are reused after a restart. An interrupted partial file is retransferred. The network path does not change source identity checks, destination SHA-256 validation or final reads through FUSE. Both Sparks transferred the real 128,587,536-byte Qwen3.8 NVFP4 final shard directly to Athena, matching SHA-256 `0d49b0cf7c15bf9b5bd1e17403b36727315860a8f2a2e0f5a53655547084f640`. A request outside the allowlist was rejected. These small qualification transfers establish the path and integrity checks, not sustained 10 GbE throughput.

### v0.8.3 import workaround

After sharing, repo blob entries become symlinks. Repeating `nest hf import` can try to adopt a regular input blob onto an existing symlink and fail with `not a regular file`, while still returning exit zero. The loader instead prepares a portable cache, hashes existing destination blobs, removes only duplicate staging links, then uses `nest --json import <cache> /hf-home/hub --seal blobs --wait`, followed by `nest --json hf share <repo> --verify --wait`. Snapshot symlinks are retained even when duplicate blob links are omitted from staging; after import they resolve against the already archived blobs. Top-level failures, host failures and import errors are checked; a full FUSE hash check is required before recording success.

## Approved Mac cache cleanup

On November 8 the owner approved removal of eight previously reviewed Mac HF cache entries plus the uv package cache. HF reported eight repos/eight revisions removed, totaling 49.0 GB logically; uv removed 635,368 files, reported as 25.2 GiB. Every model candidate passed a fresh Mac file-identity check, retained ordinary Athena files matched their captured identities, and SparkNest had already passed full FUSE verification. A real BGE weight was also restored from Athena's ordinary cache without SparkNest and matched SHA-256 before cleanup. These ordinary Athena copies and SparkNest share the NAS failure domain.

The approved HF repos were `DaoCloud/Muse-Glimmer-30B-DSpark`, `Qwen/Qwen3.5-9B`, `hubertsiuzdak/snac_24khz`, `meta-models/Muse-Glimmer-30B-ExecuTorch-PTE`, `mlx-community/Muse-Glimmer-30B-4bit`, `unsloth/bge-small-en-v1.5-GGUF`, `unsloth/orpheus-3b-0.1-ft-GGUF`, and `unslothai/Qwen3-ASR-1.7B-GGUF`. Cleanup used `hf cache rm` and `uv cache clean`; no raw shared-blob deletion was used. The remaining eight local HF revisions and 83 checked untargeted HF payloads were unchanged. LM Studio, ComfyUI, Ollama, training outputs and the active ingest runtime were retained. This approval does not authorize another batch of source deletions.

The audit, exact command and before/after cache listings live in the Mac runtime directory as `mac-cleanup-candidates.json`, `cleanup-executed.json`, and `cleanup-cache-before.json`/`cleanup-cache-after.json`. The old inventory remains evidence of captured sources even when these approved Mac cache files no longer exist. Completed Athena staging receipts are still available to the running worker.

The owner separately approved a second batch: `unsloth/MiniMax-H3-GGUF`, `unsloth/Z-Image-Turbo`, and `unsloth/Qwen3.8-27B-GGUF`. On November 8 at 16:40 UTC, `hf cache rm` reported all three repos/revisions removed, totaling 92.7 GB logically. Their full SparkNest verification had passed; fresh preflight confirmed unchanged Mac sources and all 25 ordinary Athena files against captured identities and checksums. The five remaining local HF revisions and refs matched the pre-deletion listing, and all 58 remaining snapshot payloads matched their captured source identities. The runtime files `cleanup-next-batch-readiness.json`, `cleanup-second-batch-executed.json`, and `cleanup-second-cache-before.json`/`cleanup-second-cache-after.json` retain the audit. No other model stores, staging receipts, or backup snapshots were removed. Available space remained about 234.2 GiB immediately afterward; this batch also produced no immediate physical-space increase.

The owner approved a third batch consisting only of `unsloth/Qwen3.8-27B`. On November 8 at 17:16 UTC, `hf cache rm` reported one repo/revision removed, totaling 55.6 GB logically. Full SparkNest verification had passed, and fresh preflight checked unchanged Mac files and all 32 ordinary Athena files against their captured identities and checksums. The four retained local HF revisions and refs were unchanged, as were all 26 remaining snapshot payloads. The runtime audit is `cleanup-third-batch-executed.json`, with `cleanup-third-cache-before.json`/`cleanup-third-cache-after.json`. Available space remained approximately 230.6 GiB. The recurring monitor records this removal so it will not propose the already removed cache again. No other source or backup data was removed.

Physical free space stayed near 235.5 GiB immediately after the first batch; the tools' logical deletion totals are not a measured physical-space gain. The Data volume has 13 purgeable local Time Machine snapshots and both cache paths are included in backups, consistent with APFS retaining deleted cache blocks. No backup snapshots were removed. macOS can reclaim local snapshot space as needed; manual snapshot thinning would be a separate change to backup history.

## Inspection and recovery

### Confirmed local model cleanup

The owner's November 8 follow-up authorizes removing completed Mac model copies after confirming their NAS backup, rather than requesting another approval for each model. Keep active ingest inputs, unique training outputs, new or changed files, and anything whose recovery check fails. The first standing-policy cleanup covered the 17 completed LM Studio collections, the three captured ComfyUI weights, and the remaining captured HF snapshots for Qwen3.5-4B-Base, kev-4b and Krea-2-Turbo. A later captured store requires its own matched recovery evidence before removal.

Before removing a source, capture and retain a SparkNest metadata snapshot, reconstruct the full namespace with the native offline exporter in a network-disabled one-shot container, and require no missing objects. The full namespace preserves the relative HF shared-store links. Use `--link` with the same parent data bind to retain the immutable object generations as ordinary named files without allocating another full payload copy. Keep this export and its metadata under Athena's `recovery/mac-cleanup-20261108`; do not clean them with ingestion staging. They share Athena's failure domain.

Read and hash every selected restored file against the source receipts, require all resolved links to remain within the exported tree, and qualify a real weight restored to the Mac from that plain export without the daemon or FUSE. Preserve source-to-archive filename mappings and the results on both machines. Immediately before each collection's removal, check the local file identities again. Remove only inventoried files from the approved LM Studio/ComfyUI roots, retain any unexpected files, and use `hf cache rm` for the approved HF snapshots. Verify that unrelated Ollama and training inputs remain unchanged. A completed content import alone is not the cleanup gate; the selected recovery checks must pass.

The November 8 export reconstructed 824 directories, 2,453 regular files (1,617,650,045,038 logical bytes) and 1,888 symlinks with no missing objects. The pinned one-shot image had no network and needed no daemon or FUSE. A real 1,631,205,312-byte Muse GGUF restored from this ordinary export to the Mac matched its captured SHA-256. Five safety checks covered valid sources, changed files, unexpected additions, symlinks and preserving unrelated files. `confirmed-model-cleanup.py` completed all 21 captured collections at 20:45:43 UTC, removing 402,530,853,057 logical bytes after full restored-file hashing and unchanged-source checks. There were no errors, and protected Ollama/training identities were unchanged. `mac-model-cleanup-plan.json`, `mac-model-cleanup-worker.json` and `mac-model-cleanup-executed.json` preserve scope, process identity, per-collection checks/removal outcomes and actual free space. Physical available space was 216.2 GiB at completion, 4.8 GiB lower than before the run; logical deletion is not measured physical reclamation. NAS evidence and original filename mappings are retained beside the export. Do not rerun this completed coordinator.

Ollama subsequently passed full FUSE verification of all 1,526 namespace files (111,009,895,432 logical bytes). A new network-disabled full export under Athena's `recovery/mac-cleanup-ollama-20261108` reconstructed 838 directories, 3,979 regular files (1,728,659,940,470 bytes) and 1,888 symlinks with no missing objects. Its own retained `meta.sqlite`, `offline-export.json` and source mappings are distinct from the earlier export. A real 2,471,922,052-byte ExecuTorch weight from the captured `llama3.2-et` model was restored from the plain export to the Mac and matched SHA-256; the disposable test copy was then removed. `confirmed-ollama-cleanup.py` is now hashing every restored namespace path and checking portable internal links before rechecking original identities, absence of unexpected files, idle Ollama/no open source files, protected training inputs and unfinished ingest dependencies. It will remove only the 1,525 captured regular source paths (89,843,137,704 bytes); the namespace total counts one 21.2 GB payload twice. The existing `.studio_links` symlink and directories remain. `ollama-cleanup-plan.json`, `ollama-cleanup-worker.json`, `ollama-cleanup-executed.json`, `ollama-cleanup-worker.log`, `ollama-cleanup-restore-sample.json` and `ollama-recovery-verified.json` preserve progress and acceptance evidence; the last file is written after full recovery verification. Check the actual process and report before acting; never start a duplicate or implicitly rerun a held/partial result. Four source-guard checks passed before this one-shot coordinator started.

The 15-minute task completes the selected recovery checks and follows through on the owner's standing cleanup instruction. It must not wait for all 98 collections or global deduplication acceptance to remove a confirmed Mac duplicate. Create a new matched snapshot/export for each later selection; an earlier tree cannot prove recovery of subsequently imported data. Retain every export and its metadata. Independent backup outside Athena remains separate work.

```sh
sudo /usr/local/bin/docker exec sparknest-sparknest-1 nest --json status
sudo /usr/local/bin/docker exec sparknest-sparknest-1 nest --json jobs
sudo /usr/local/bin/docker exec sparknest-sparknest-1 nest --json fsck --deep --recheck
```

Deep fsck covers recognized HF SHA-256 blobs, not every arbitrary imported file. `--recheck` forces previously verified copies to be read again. The bulk loader's full-file verification covers all its expected files independently of that format-specific check.

Stop only this project with `sudo /usr/local/bin/docker compose -f /volume1/docker/sparknest/compose.yaml down`. Persistent directories and originals remain. Stop the coordinator before taking the service down.

A recovery archive can be registered with `nest store add recovery /data/recovery/archive --gateways athena` after creating that directory. `nest backup create <selection> --name <name> --store recovery --wait` retains that selection's objects; `nest backup meta --store recovery` writes a metadata snapshot. The snapshot and object set must describe the same state: quiesce imports/writes while capturing a full archive and its metadata. A later metadata snapshot can name files added after a selection's backup and therefore absent from that backup's objects.

Offline `sparknestd export` runs in a one-shot container with `--network none`, read-only metadata/object binds, and a fresh writable output directory. It needs no running daemon or FUSE device. Exporting the full namespace preserves relative HF links to the shared blob store; subtree exports place outside targets under `_outside` and need care with those links. Always inspect the missing-object report and hash exported model files. Local archives do not protect against losing Athena; independent backup and full collection acceptance remain tracked in #1371.

## Recorded acceptance

Observed on Athena: FUSE ABI 7.23, UI HTTP 200, API 401 without a token, real BGE GGUF and SNAC import/share/read, initial deep fsck with no findings, service recreation/remount with persistent content and token, and import/read of the 2.34 GB DFlash2 weights. The same-bind native imports report zero payload-copy bytes. The real flatrate adapter and tokenizer files passed full FUSE SHA-256 verification. A DFlash2 revision import reused two existing blobs, created five snapshot symlinks and verified all six expected paths, exercising the filtered-staging workaround.

A network-disabled one-shot export reconstructed a frozen metadata snapshot as 70 directories, 66 files (5,178,805,597 bytes) and 35 symlinks, using the read-only live object store plus the small retained recovery archive. Twenty exported source-checksummed paths totaling 4,839,937,827 bytes matched their recorded SHA-256. This proves reconstruction without the daemon, not completeness of the small retained archive: an earlier archive-only attempt missed a source ref added after that selection was backed up.

Bulk ingest, final sharing/space accounting, complete recovery acceptance, and any further source cleanup are not complete. Consult the runtime journal and #1371 for current progress.
