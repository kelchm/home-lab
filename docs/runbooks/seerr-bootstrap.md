# Seerr Bootstrap

Seerr is the household request and discovery frontend for the media stack. Manifests live at `kubernetes/apps/media/seerr/`; everything below is the state that Flux *cannot* manage, because Seerr keeps it in `/app/config/settings.json` on its Longhorn PVC.

Seerr accepts no environment variable for the Jellyfin, Radarr or Sonarr connections — only `TZ`, `LOG_LEVEL`, `PORT` and its own `API_KEY`. The config volume is therefore the system of record for those credentials, and `media/arr-api-keys` is *not* wired into Seerr (Seerr is in the same class as Bazarr: UI-configured).

## Split of state

| State | Lives in | Managed by |
|---|---|---|
| Chart version, image tag, resources, PVC, route, network policy | `kubernetes/apps/media/seerr/` | Flux + Renovate |
| Jellyfin / Radarr / Sonarr URLs + API keys | `/app/config/settings.json` | this runbook |
| Default permissions, global quotas, sign-in policy | `/app/config/settings.json` | this runbook |
| Users, requests, issues, sessions | `/app/config/db/db.sqlite3` | Seerr |
| Per-user permission and quota **overrides** | `/app/config/db/db.sqlite3` | Seerr |

Default permissions are copied onto a user at creation, so the settings row and the user rows drift apart by design — restoring `settings.json` alone does not restore who was allowed to request what.

Both files sit on PVC `seerr-config`, which joins Longhorn's implicit `default` recurring-job group automatically — `backup-daily` (retain 7) and `backup-weekly` (retain 4) cover it with no per-app manifest work. See [longhorn-backup-restore](longhorn-backup-restore.md).

## First run — order matters

The setup wizard is reachable at `https://seerr.home.kelch.io` on a fresh PVC; everything redirects to `/setup` until it completes.

1. **Sign in with a Jellyfin *administrator* account.** Seerr hard-requires `User.Policy.IsAdministrator` on this first login and returns 403 `NotAdmin` otherwise. This account becomes owner (user id 1) permanently and cannot be deleted. Choose deliberately.

   Setup mints a *separate* Jellyfin API key (`POST /Auth/Keys?App=Seerr`) and stores it as `jellyfin.apiKey` in `settings.json`. That key — paired with the owner's Jellyfin user id — is what every server-side library scan uses from then on, **not** the owner's login token. Signing in again as the owner refreshes only that user's access token; it does not remint the API key. Revoking the `Seerr` key in Jellyfin therefore breaks library sync until the key is replaced in `settings.json` or the setup path is re-run.

   Connection fields: hostname `jellyfin.media.svc.cluster.local`, port `8096`, no SSL, empty URL base. Seerr mints its own Jellyfin API key (`POST /Auth/Keys?App=Seerr`) during this step.

   Afterwards set **External URL** to `https://jellyfin.home.kelch.io` under *Settings → Media Server*. That field is what Seerr hands to the browser for "Play on Jellyfin" links; left empty it falls back to the internal address, which no household client can resolve.

2. **Sync libraries and enable at least one.** The wizard's Continue button is gated on one enabled library.

3. **Skip the Radarr/Sonarr step** — wire them in step 5 below, where the settings are easier to check.

4. **Finish Setup.** This sets `public.initialized = true`.

## Settings → General

- **Application URL** — `https://seerr.home.kelch.io`. Password-generation emails and generated links break without it.
- **Enable Proxy Support** — **on**. Off (the default) means every client IP Seerr logs, and every `X-Forwarded-For` it passes to Jellyfin, is the Traefik hop. It also leaves the session cookie without `Secure`, because Express resolves `secure: 'auto'` from `req.secure` and TLS terminates at Traefik.
- **Enable CSRF Protection** — leave **off**. Turning it on forces HTTPS-only access with `sameSite: strict` and blocks external API writes; the documented recovery is hand-editing `settings.json` on the PVC.
- **Version Check** — leave on. `api.github.com` is inside the allowed egress range.

## Settings → Services

Both servers are reached over the cluster network. The `seerr` tag must already exist in Radarr/Sonarr before it appears in Seerr's tag picker — create it first under *Settings → Tags* in each.

**Radarr**

| Field | Value |
|---|---|
| Hostname | `radarr.media.svc.cluster.local` |
| Port | `7878` |
| API key | Radarr → Settings → General → API Key (same value as `RADARR_API_KEY` in `media/arr-api-keys`) |
| Root folder | `/data/movies` |
| Quality profile | `HD Bluray + WEB` |
| Minimum availability | `Released` |
| Tag | `seerr` |
| Default server | yes |
| Enable 4K | no |

**Sonarr**

| Field | Value |
|---|---|
| Hostname | `sonarr.media.svc.cluster.local` |
| Port | `8989` |
| API key | Sonarr → Settings → General → API Key (same value as `SONARR_API_KEY`) |
| Root folder | `/data/tv` |
| Quality profile | `WEB-1080p` |
| Season folders | on |
| Tag | `seerr` |
| Default server | yes |
| Enable 4K | no |

The two profiles are the ones Recyclarr owns and re-syncs daily at 03:00 America/New_York — any other profile drifts unmaintained. `UHD Bluray + WEB` / `WEB-2160p` are the maintained 4K alternatives if the household turns out to be all direct-play; changing the profile is a dropdown here and affects nothing in Git. Weigh it against Jellyfin hardware transcoding being disabled (see issue #63), which makes a 4K grab direct-play-or-CPU-transcode.

Seerr's *4K request path* is a different thing and stays off: it expects a second Radarr and Sonarr instance designated as the 4K servers, which this cluster does not have.

Do not offer `/data/books` or `/data/audiobooks` as root folders — neither has a writer ACE since Readarr was removed.

## Settings → Users

Set these **before** importing anyone; `Default Permissions` is copied onto each user at creation and changing it later has no effect on existing users.

- **Default Permissions** — `Request` + `Request Movies` + `Request Series` only. Specifically not: Auto-Approve, Manage Requests, Manage Users, Manage Issues, Advanced Requests, or any 4K permission. Advanced Requests is the one that exposes the root-folder and profile pickers to the requester, which would route around the profile decision above.
- **Global Movie Request Limit** — `10` per `7` days.
- **Global Series Request Limit** — `3` per `7` days. This counts *seasons*, not shows.
- **Enable New Jellyfin Sign-In** — **off**. On, any Jellyfin account is auto-created in Seerr on first login, making the Jellyfin user table the authorization boundary.
- **Enable Local Sign-In** — leave **on** (see break-glass below).

Then *Users → Import Jellyfin Users* and select the specific accounts to admit.

## Break-glass administrator

Create one local admin under *Users → Create Local User* with a generated password, and give no household account a local password.

Local sign-in stays enabled deliberately. The owner account is bound to whichever Jellyfin admin signed in at setup, so disabling local sign-in makes Seerr admin access depend on Jellyfin being up and on its user DB matching what Seerr recorded.

There is no CLI recovery path. If local sign-in is ever turned off and Jellyfin then becomes unavailable, `settings.json` has to be edited directly on the PVC. `kubectl debug --target` is *not* the tool for this — an ephemeral container does not inherit the target's volume mounts, so `/app/config` would not be there. The config PVC is RWO, so the StatefulSet has to release it first:

```bash
kubectl -n media scale statefulset/seerr --replicas=0
kubectl -n media wait --for=delete pod/seerr-0 --timeout=120s

kubectl -n media apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: seerr-recovery
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  containers:
    - name: shell
      image: ghcr.io/seerr-team/seerr:v3.4.1
      command: ["sleep", "600"]
      volumeMounts:
        - name: config
          mountPath: /app/config
  volumes:
    - name: config
      persistentVolumeClaim:
        claimName: seerr-config
YAML

# Edit structurally — the file is pretty-printed, so a substring sed is unreliable.
kubectl -n media exec seerr-recovery -- node -e '
  const fs = require("fs"), f = "/app/config/settings.json";
  const s = JSON.parse(fs.readFileSync(f, "utf8"));
  s.main.localLogin = true;
  fs.writeFileSync(f, JSON.stringify(s, null, 2));
'

kubectl -n media delete pod seerr-recovery
kubectl -n media scale statefulset/seerr --replicas=1
```

Store the break-glass password wherever the other operator credentials live; it is not needed by any workload, so it does not belong in a SOPS Secret that Flux mounts.

## Credential rotation

Rotating a Radarr or Sonarr API key is a three-step operation because the key now exists in two places:

1. Rotate in the *arr UI (Settings → General → API Key).
2. Update `kubernetes/apps/media/arr-api-keys.sops.yaml` (`sops --encrypt --in-place …`), commit; Unpackerr and Recyclarr pick it up on the next reconcile.
3. Re-enter it in Seerr under *Settings → Services*, or `PUT /api/v1/settings/radarr/<id>` with an `X-API-Key` header.

Seerr's *own* API key can be pinned declaratively via the `API_KEY` env var — it is re-asserted on every settings load — if scripted configuration is ever wanted. It is not set today.

## Operational notes

- **`Manage Users` silently exempts its holder from all request quotas**, and `Admin` short-circuits every permission check rather than being one more flag. Neither belongs on a household account.
- **Egress is default-deny.** `networkpolicy.yaml` allows DNS, Jellyfin, Radarr, Sonarr and public 80/443. Notification agents that fit in 80/443 (Discord, Telegram, Gotify, Pushover, generic webhook) work as-is; **SMTP email will not** — 587/465 needs an explicit port added to the policy.
- **No base URL support.** Seerr must own a hostname; a `/seerr` path prefix on a shared listener is not configurable.
- **Do not put the config volume on NFS.** SQLite runs with WAL enabled and needs real file locking. It is on Longhorn RWO, which is correct — keep it single-replica.
- **`readOnlyRootFilesystem` is not enabled.** The chart defaults it off. Upstream blesses it, but `npm start` wants a writable `HOME` and it is unverified against this image; enabling it needs a canary plus an emptyDir at `/tmp`.
- **The `app.kubernetes.io/version` pod label reads `v3.4.0`** — it comes from the chart's `appVersion`, while the image is pinned to `v3.4.1`. Cosmetic; the running image is what `image:` says.
- **The container is named `seerr-chart`, not `seerr`.** The chart hardcodes it to `.Chart.Name`, which `nameOverride` does not affect — so `kubectl logs -n media seerr-0 -c seerr` fails. The pod is single-container, so just drop `-c`.
- **Seerr is in the no-Synology-user class.** It never touches the library — all availability data comes over the Jellyfin API. See [arr-suite-bootstrap](arr-suite-bootstrap.md).
