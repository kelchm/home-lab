# Unpackerr

Unpackerr polls the Sonarr, Radarr and Lidarr queues and extracts completed downloads in place under `/data/.downloads`. It mounts only the downloads subdirectory of `media-library`; the Starr apps import into the library. It has no watched-folder configuration or HTTPRoute.

## Configuration and credentials

Git and environment variables own the configuration. There is no `/config` volume or log file, so [v1.0.1 keeps history in memory](https://github.com/Unpackerr/unpackerr/blob/v1.0.1/pkg/unpackerr/historyfile.go); UI edits and restart-resume history are not persisted. A config volume is only needed if that operating model changes.

The cluster-only listener on port 5656 serves metrics and the v1 UI/API. [Upstream registers them together](https://github.com/Unpackerr/unpackerr/blob/v1.0.1/pkg/unpackerr/webserver.go): disabling the listener also disables metrics. The UI shell is public to clients that can reach the Service, while the API requires authentication. The random UI password is supplied by `unpackerr-auth` from `app/secret.sops.yaml`, preventing a temporary password from appearing in collected startup logs. The UI is not an operational configuration interface for this deployment.

The same Secret holds a separate 96-character random metrics key. Its role grants only `system:metrics:read`. The ServiceMonitor selects that key for Bearer authentication; it cannot read or change application configuration. The liveness probe uses the image's bundled `curl` to fetch authenticated metrics locally, retaining the application-statistics check without embedding a credential in a probe manifest. v1.0.1 has no dedicated health endpoint. The shell command uses `$UN_WEBSERVER_API_KEYS_0_KEY` without braces so Flux does not substitute it before the container runs.

Rotate either credential by editing the SOPS Secret and replacing it with a cryptographically random value; metrics keys must be 60–150 ASCII characters. After Flux applies the changed Secret, restart the Deployment in an authorized rollout because the application reads these values from its environment at startup. The scraper reads its credential from the same Secret. Never copy credentials into PR comments or logs. An unused admin API key is generated in memory on each start; [upstream logs its name, not its value](https://github.com/Unpackerr/unpackerr/blob/v1.0.1/pkg/unpackerr/apikeys.go).

## v1 upgrade acceptance

The [v1 upgrade guide](https://unpackerr.zip/docs/install/upgrading/) describes the new extractor behavior. Starr's ratio cap is hardcoded to 7.5:1 in v1.0.1, up from 5:1, and nested archives no longer count their own archive bytes toward that ratio. There is no environment setting to restore the previous Starr cap. These changes can allow downloads to extract that v0.16.1 rejected; `.asar` support is part of the new extractor, though the Starr allowlist still excludes it.

Before merging the v1 upgrade, the owner must accept both the authenticated cluster-internal UI/API surface and the extractor changes. After an authorized rollout, verify that the configured image is running, liveness remains healthy, VictoriaMetrics scrapes Unpackerr successfully, and a real Starr-managed archive extracts and imports. Local fixtures do not validate the NAS permissions or live Starr integration.
