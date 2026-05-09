# Kanidm Bootstrap

One-time procedure to make a freshly-deployed Kanidm pod usable. Runs *after* the StatefulSet from `kubernetes/apps/auth/kanidm/` is `Ready` but before any OIDC RP can authenticate against it.

## What this gets you

- A real password for `idm_admin` (the IDM administrator — manages users, groups, OAuth2 clients).
- Optionally a real password for `admin` (the system administrator — replication, recovery).
- A populated `kanidm-bootstrap` Secret in the `auth` namespace.
- The `kanidm-seed` Job creates the `arr-admins` group, the `operator` person account, and the `arr-suite` + `jellyseerr` OAuth2 clients.
- OAuth2 client_secrets that *consumers* can capture and store in their own SOPS Secrets (Kanidm doesn't let you set client_secrets to chosen values — server-generated only).

## Why this is manual

Kanidm has no init flow that lands a known admin password — both `idm_admin` and `admin` start with no credentials and require `kanidmd recover-account` to be run *inside the pod* to print a one-time recovery password. There's no way to wire that into a fully-declarative GitOps flow short of writing a custom controller.

## Prerequisites

- `kubernetes/apps/auth/kanidm` Flux Kustomization is `Ready=True`.
- Kanidm StatefulSet has at least one `Ready` pod: `kubectl -n auth get sts kanidm` shows `1/1`.
- `auth.home.kelch.io` resolves and serves a valid cert: `curl -I https://auth.home.kelch.io/status` returns 200.

## Step 1 — Recover idm_admin password

```sh
kubectl -n auth exec sts/kanidm -- \
    kanidmd recover-account idm_admin -c /data/server.toml
```

The command prints a recovery password to stdout. **Capture it now** — it's not retrievable later.

Repeat for the system admin if you want it ready (you usually don't need it day-to-day):

```sh
kubectl -n auth exec sts/kanidm -- \
    kanidmd recover-account admin -c /data/server.toml
```

## Step 2 — Populate the kanidm-bootstrap Secret

```sh
sops kubernetes/apps/auth/kanidm/app/secret.sops.yaml
```

Replace the `REPLACE_WITH_*` placeholders with the recovered passwords. Save and exit — sops re-encrypts in place.

Commit and push:

```sh
git add kubernetes/apps/auth/kanidm/app/secret.sops.yaml
git commit -m "Populate kanidm-bootstrap Secret with recovered passwords"
git push
```

## Step 3 — Wait for the seed Job to run

Reloader watches `kanidm-bootstrap` and re-creates the `kanidm-seed` Job whenever the Secret changes. After Flux applies the updated Secret:

```sh
kubectl -n auth get jobs -w
# wait for kanidm-seed to show 1/1 Complete
kubectl -n auth logs job/kanidm-seed
```

The Job is idempotent — re-running it is a no-op for objects that already exist. To force a re-run after editing the Job manifest:

```sh
kubectl -n auth delete job kanidm-seed
# Flux recreates on next reconcile (1h interval, or `flux reconcile ks kanidm -n auth`)
```

## Step 4 — Capture OAuth2 client secrets for downstream consumers

Kanidm generates the OAuth2 client_secret server-side; we capture it after the seed Job creates the client and store it in each consumer's namespace.

```sh
kubectl -n auth exec sts/kanidm -- env KANIDM_URL=https://auth.home.kelch.io \
    kanidm system oauth2 show-basic-secret arr-suite --name idm_admin

kubectl -n auth exec sts/kanidm -- env KANIDM_URL=https://auth.home.kelch.io \
    kanidm system oauth2 show-basic-secret jellyseerr --name idm_admin
```

(Login with `kanidm login --name idm_admin` first; the cached token persists for the pod's lifetime.)

Store each secret in the appropriate consumer Secret — see PR 6 (traefik-oidc-auth plugin → arr-suite) and the Jellyseerr PR for the exact paths.

## Step 5 — Sanity check via web UI

```
https://auth.home.kelch.io/ui
```

Log in as `idm_admin`. You should see:
- Groups → `arr-admins` exists, `operator` is a member.
- Persons → `operator` exists.
- OAuth2 → `arr-suite` and `jellyseerr` clients exist with scope maps for `arr-admins`.

Set a permanent password for `operator` (the seed Job doesn't — Kanidm's password-set flow requires user-driven WebAuthn enrollment for security):

```sh
# Reset operator's password via idm_admin (prints a one-time recovery password)
kubectl -n auth exec sts/kanidm -- env KANIDM_URL=https://auth.home.kelch.io \
    kanidm person credential update operator --name idm_admin
```

Or just have the housemate log in with a recovery password and set their own.

## Troubleshooting

**Seed Job pod stuck `CrashLoopBackOff` with auth errors.** Most likely the password in the SOPS Secret is stale (recovered, then someone changed it via the UI). Re-run step 1 and step 2.

**`auth.home.kelch.io` returns the gateway's default cert, not Kanidm's.** Means the BackendTLSPolicy isn't being honored — check `kubectl get backendtlspolicy -n auth -o yaml` and confirm the gateway-services Traefik replica picked it up. Traefik logs in `network` namespace will show the upstream connection failure.

**`kanidm login` hangs in the Job.** The kanidm CLI may be waiting for TTY. The seed Job pipes the password via stdin and sets `KANIDM_PASSWORD`; if the CLI version in the image ignores both, the workaround is to bake a token-cache file at build time. So far this hasn't been observed on `kanidm/tools:1.10.x`.

**Need to start over from scratch.** Delete the StatefulSet's PVC (`kubectl -n auth delete pvc data-kanidm-0`), let Flux re-create the StatefulSet, and re-run from step 1. **This wipes Kanidm's database** — every group, person, OAuth2 client, and stored credential is gone.
