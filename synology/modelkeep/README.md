# ModelKeep on Athena

[ModelKeep](https://github.com/kaznak/modelkeep) v0.4.12 is a pull-through mirror for public Hugging Face repositories. A LAN client points `HF_ENDPOINT` at Athena and requests the original repository IDs. The first request for a file downloads it from Hugging Face through the official client; later requests from any device are served from the archive on Athena, including while Hugging Face is unreachable. [home-lab#608](https://github.com/kelchm/home-lab/issues/608) records the evaluation that selected it.

doco-cd deploys this project from `main`; the [Synology README](../README.md) describes the deployment model, rollback, and break-glass apply.

## Endpoints

| Address | Clients |
|---|---|
| `http://10.32.10.5:8092` | Main VLAN devices |
| `http://10.32.25.5:8092` | Storage VLAN hosts, such as the Sparks |

The download port has no authentication, and any client that reaches it can make Athena download public repositories. There is no management listener and no Hugging Face token, so gated and private repositories are unavailable.

## Client configuration

```sh
export HF_ENDPOINT=http://10.32.10.5:8092
export HF_HUB_DISABLE_XET=1
hf download Qwen/Qwen2.5-0.5B-Instruct
```

ModelKeep never redirects clients to Hugging Face's CDN, and the evaluation's Xet-enabled clients also stayed on the mirror; `HF_HUB_DISABLE_XET=1` is the upstream-recommended setting. llama.cpp reads `MODEL_ENDPOINT`, falling back to `HF_ENDPOINT`.

## Behavior to know

- **Large files that are not archived yet.** ModelKeep answers `503` after 8 seconds while it keeps downloading, and a retry joins that download without fetching anything again. `huggingface_hub` retries on its own, but a multi-gigabyte file can exhaust its retries, and llama.cpp fails on the first `503`. Re-running the same command succeeds once the file is archived. Archive large models ahead of use with the [warm-up procedure](#archive-a-model-ahead-of-use).
- **Pin a revision for reproducible results.** Requesting a file that the archived `main` does not hold can move `main` to Hugging Face's current commit. Requests by commit hash are unaffected.
- **Wildcards acquire every matching file.** A request path containing `*` is passed to the downloader as a pattern.
- **Integrity.** ModelKeep hashes and verifies the bytes it stores, but does not compare them with Hugging Face's published hashes. [Check the archive against upstream hashes](#verify-against-upstream-hashes) after large acquisitions.

## State

`/volume1/models/modelkeep` on the checksummed `models` shared folder holds the archive as ordinary files, readable without ModelKeep:

```text
/volume1/models/modelkeep/
├── models/<org>/<repo>/
│   ├── revisions/<commit>/     # payload files, .modelkeep-manifest.json, .modelkeep-upstream-files.json
│   └── refs/<name>             # commit a mutable ref resolves to
├── tmp/                        # in-progress acquisitions
└── cache/xet/                  # official client's chunk cache
```

The container runs as the DSM service account `modelkeep` (`1045:100`). The directory must exist, owned by that account and with an access entry for it, before the first deployment:

```sh
ssh kelchm@10.32.20.5 '
  set -eu
  sudo mkdir /volume1/models/modelkeep
  sudo chown modelkeep:users /volume1/models/modelkeep
  sudo /usr/syno/bin/synoacltool -add /volume1/models/modelkeep user:modelkeep:allow:rwxpdDaARWc--:fd--
'
```

The archive can be rebuilt by downloading again, so it is not backed up.

## Archive a model ahead of use

List the repository's files through the mirror, pin the commit, and request each wanted file until it is archived. The `include` expression selects files by path:

```sh
ssh kelchm@10.32.20.5 '
  set -eu
  ep=http://10.32.10.5:8092
  repo=Qwen/Qwen2.5-7B-Instruct
  include="\\.(json|safetensors|txt)$"
  sha=$(curl -fsS "$ep/api/models/$repo/revision/main" | jq -r .sha)
  curl -fsS "$ep/api/models/$repo/tree/$sha?recursive=true" |
    jq -r --arg re "$include" ".[] | select(.type == \"file\" and (.path | test(\$re))) | .path" |
    while IFS= read -r file; do
      until curl -fsS -I -o /dev/null "$ep/$repo/resolve/$sha/$file"; do sleep 30; done
      echo "archived $file"
    done
'
```

## Verify against upstream hashes

Each revision records Hugging Face's LFS SHA-256 for its files. This compares them with ModelKeep's manifest and prints only mismatches; small files stored directly in Git are not covered:

```sh
ssh kelchm@10.32.20.5 '
  cd /volume1/models/modelkeep/models
  for r in */*/revisions/*/; do
    sudo jq -r --slurpfile m "$r.modelkeep-manifest.json" \
      ".files[] | select(.lfs_sha256) | . as \$u | (\$m[0].files[] | select(.path == \$u.path) | .sha256) as \$s | select(\$s) | \"\(if \$s == \$u.lfs_sha256 then \"match\" else \"MISMATCH\" end) $r\(\$u.path)\"" \
      "$r.modelkeep-upstream-files.json"
  done | grep -v "^match" || echo "all LFS files match"
'
```

A mismatch means the archived file differs from what Hugging Face published; investigate before relying on that revision.

ModelKeep's own `audit` re-hashes every archived file against its manifest:

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker exec modelkeep-modelkeep-1 /bin/modelkeep audit /data'
```

## Updates

Before merging an image update, read the release notes and the repository's `docs/adr/` changes for archive-format decisions. The project is young and changes quickly.
