# ModelKeep retirement

ModelKeep is deprecated and removed from this repository's managed deployments at the owner's request on 2026-11-17. It is no longer a supported Hugging Face mirror. Remove any client `HF_ENDPOINT` or `MODEL_ENDPOINT` override pointing at its former endpoints, `http://10.32.10.5:8090` and `http://10.32.25.5:8090`.

The [deployment issue](https://github.com/kelchm/home-lab/issues/634) and [NAS cache evaluation](../../docs/evaluations/hugging-face-nas-cache.md) retain the historical results and limitations. The upstream source became inaccessible, so the proposed v0.4.13 update was never qualified or deployed.

## Retained archive

Retirement removes the service, not its model files. Keep `/volume1/models/modelkeep`, its manifests, refs, payloads and cache directories. The DSM account `modelkeep` (UID 1045, group `users`/GID 100) and its ACLs remain so these files retain their existing ownership and access.

```text
/volume1/models/modelkeep/
├── models/<org>/<repo>/
│   ├── revisions/<commit>/
│   └── refs/<name>
├── tmp/
└── cache/xet/
```

The files can be read without the service. [SparkNest's inventory loader](../sparknest/ingest.py) retains support for ModelKeep manifests as an offline import format; it does not call the mirror. This does not establish that every retained file has been imported into SparkNest, that a partial revision is a complete model, or that a separate backup exists. Keep the original data until any future deletion is separately approved and its required copies verified.

## Finish the live retirement

Removing the target from [`.doco-cd.yml`](../.doco-cd.yml) prevents future deployments but does not stop existing containers. After the retirement commit reaches `main`:

1. Confirm Athena's doco-cd has fetched that revision and resolved its six remaining targets. Check that no ModelKeep deployment is in progress before stopping it; otherwise an already-started deployment could recreate the container.
2. Confirm no downloads or client connections are active on port 8090. Keep a private copy of the [last tracked Compose definition](https://github.com/kelchm/home-lab/blob/ffb9f96278042af60ae514f830e72939ebe54072/synology/modelkeep/compose.yaml) on Athena for shutdown and recovery.
3. Set `MODELKEEP_COMPOSE` to that staged file and remove only this Compose project:

   ```sh
   sudo /usr/local/bin/docker compose -p modelkeep -f "$MODELKEEP_COMPOSE" down --timeout 30
   ```

   Do not pass `--volumes`, remove the archive directory or DSM account, or prune the retained image.
4. Verify there are no running or stopped containers with `com.docker.compose.project=modelkeep`, no port 8090 listeners or published mappings, and `/volume1/models/modelkeep` still has its previous ownership and directory identity. Confirm doco-cd and the other NAS workloads remain healthy and that a later poll does not recreate ModelKeep.

The last deployed image was `ghcr.io/kaznak/modelkeep:v0.4.12@sha256:ce4a960abe4068a1f328e68627f6a8ecfb6f26349fc22438d8e139e62a3070c0`. Its presence on Athena was verified on 2026-11-17; it is retained for recovery. Restoring a mirror requires an intentional decision to restore its Compose definition and deployment target, with the retained data mounted as before. Historical hash checks and ModelKeep's audit were different checks: its audit established consistency with its own manifest, not authenticity against Hugging Face.
