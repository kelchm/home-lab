# MatrixHub on Athena (pilot)

[MatrixHub](https://github.com/matrixhub-ai/matrixhub) v0.2.0 runs on Athena as a curated, hosted Hugging Face model library for LAN clients. This is the NAS pilot for [home-lab#608](https://github.com/kelchm/home-lab/issues/608); [home-lab#614](https://github.com/kelchm/home-lab/issues/614) holds the local evaluation it builds on. It is not yet the archive of record: keep the original Mac, Spark and NAS model copies until the export/restore check in #614 passes against this instance.

## Operating rule: hosted library only

Import selected files whose hashes you have verified against upstream, and let clients download the hosted copies. Do not create proxy projects. The evaluation reproduced three failures in v0.2.0's proxy mode:

- cached reads fail once the one-minute metadata TTL expires while Hugging Face is unreachable;
- a request for one file makes the server fetch the repository's other files (53.5 GB of unrequested variants in one test);
- proxy ingestion stores bytes without verifying their hashes.

Hosted imports get new local commits and are addressed by their MatrixHub `<project>/<model>` names, not the original Hugging Face revisions.

## Endpoints

MatrixHub listens only on Athena's loopback until the seeded admin password is replaced (see [First start](#first-start)).

| Address | Use |
|---|---|
| `http://127.0.0.1:3001` on Athena | Administration over SSH |

## State

| Path | Contents |
|---|---|
| `/volume1/hf-models-athena/matrixhub/` | All MatrixHub data: SQLite database, Git repositories and LFS objects |
| `/volume1/docker/matrixhub/config/config.yaml` | Runtime copy of [`config.yaml`](config.yaml), the upstream v0.2.0 SQLite default |

`hf-models-athena` is a dedicated, checksummed Btrfs shared folder with no user or NFS access; only the container writes to it. The one-shot `config` service copies `config.yaml` into place, and MatrixHub restarts whenever that copy is republished. Both runtime directories must exist before the first deployment:

```sh
ssh kelchm@10.32.20.5 \
  'sudo mkdir -p /volume1/hf-models-athena/matrixhub /volume1/docker/matrixhub/config'
```

## First start

The database migration seeds `admin` / `changeme`. Replace it before MatrixHub listens on the LAN, and keep the new password in 1Password. The password travels on stdin and through `printf`, so it never appears in a process list on Athena:

```sh
new=$(openssl rand -hex 24)
printf '%s\n' "$new" | ssh kelchm@10.32.20.5 '
  IFS= read -r new
  jar=$(mktemp)
  printf "{\"username\":\"admin\",\"password\":\"changeme\"}" |
    curl -fsS -c "$jar" -H "Content-Type: application/json" --data-binary @- \
      http://127.0.0.1:3001/api/v1alpha1/login >/dev/null
  printf "{\"oldPassword\":\"changeme\",\"newPassword\":\"%s\"}" "$new" |
    curl -fsS -b "$jar" -H "Content-Type: application/json" --data-binary @- \
      http://127.0.0.1:3001/api/v1alpha1/current-user/reset-password
  rm -f "$jar"
'
```

## Updates

`config.yaml` sets `database.migrate: true`, so a new image migrates the database when it starts, and a `git revert` restores the definition but not the schema. Before merging a MatrixHub image update, read the release notes and take a DSM snapshot of `hf-models-athena` (Snapshot Replication is installed); restore that snapshot to roll back.
