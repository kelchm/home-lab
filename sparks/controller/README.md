# spark-controller

Control-plane API for the DGX Spark pair. Runs as a container on spark-1 (deployed by `sparks/ansible/baseline.yaml`), fronted by Caddy at `http://spark.home.kelch.io/admin/`. It executes the repo's transaction playbooks — the same ones `task sparks:*` runs from a laptop — against a checkout of `main` that it keeps synced, and journals every job to disk. It never switches on its own: every mutation is an explicit authenticated request, and the playbook-side lock on spark-1 serializes it against the laptop path.

## API

All routes except `healthz` require `Authorization: Bearer <token>` (token in `sparks/ansible/secrets.sops.yaml`, installed to `/etc/spark-controller/token` by the baseline).

| Route | Purpose |
|---|---|
| `GET /admin/v1/healthz` | Liveness, unauthenticated |
| `GET /admin/v1/state` | Resident profile, per-port and stable-endpoint health, git sync rev, current job, switch-log tail |
| `GET /admin/v1/profiles` | Profile definitions as parsed from the synced checkout |
| `POST /admin/v1/switches` `{"profile": "glm"}` | Run the guarded switch transaction; `202` + job, `409` while any job runs |
| `POST /admin/v1/publishes` `{"profile": "glm"}` | Re-point the stable endpoint at an already-healthy profile |
| `POST /admin/v1/down` | Guarded teardown of everything on both hosts |
| `GET /admin/v1/jobs`, `GET /admin/v1/jobs/{id}?log_offset=N` | Job history; single job with its playbook log |

A switch from anywhere on the LAN:

```sh
curl -sS -X POST http://spark.home.kelch.io/admin/v1/switches \
  -H "Authorization: Bearer $SPARK_TOKEN" -H 'Content-Type: application/json' \
  -d '{"profile":"glm53-exl3"}'
```

Then poll `GET /admin/v1/jobs/<id>` for progress; a TP=2 cold boot legitimately takes 15–30 minutes.

## Development

```sh
uv sync && uv run pytest
```

The image is built by `.github/workflows/spark-controller-image.yaml` for `linux/arm64` (the Sparks are GB10/aarch64) and published as `ghcr.io/kelchm/spark-controller` with `latest` and immutable `sha-<commit>` tags.
