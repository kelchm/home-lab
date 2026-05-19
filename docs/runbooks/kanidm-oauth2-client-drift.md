# Recovering a KanidmOAuth2Client when its K8s secret drifts from kanidm

## When this happens

After creating or modifying a `KanidmOAuth2Client`, you observe that the
OIDC callback to a gated host fails with one of:

- Browser: "Failed to exchange auth code"
- `traefik-admin` logs: `[ERROR] [traefik-oidc-auth] exchangeAuthCode:
  received bad HTTP response from Provider (Status: 401)`

The kaniop-emitted Secret in the cluster holds a `CLIENT_SECRET` value
that kanidm doesn't recognise — kanidm returns 401 on its token endpoint
when traefik-oidc-auth POSTs to exchange the authorization code.

## Why it happens

kanidm runs as a multi-replica StatefulSet and its replication is
eventually consistent. kaniop's reconcile of a `KanidmOAuth2Client` is a
multi-step API conversation against a headless kanidm Service (kaniop
forces `clusterIP: None` when replication is enabled and the `Kanidm`
CR exposes no `sessionAffinity` knob). If consecutive calls land on
different kanidm pods *before* replication has converged, two pods may
each create the OAuth2 entry independently and each generate their own
random `basic_secret`. kanidm conflict-resolution then keeps one of the
two; kaniop reads `_basic_secret` from whichever pod answers next and
writes that value to the K8s Secret. The two can differ.

This is structural per the kanidm docs (multi-replica writes require
"sticky sessions or active-passive"). kaniop has no compare-and-swap,
no retry-on-conflict, and no documented procedure to keep them in
lockstep. The recovery path is to force kanidm to regenerate the secret
and let kaniop re-read it.

## Recovery

1. **Verify the drift.** Probe kanidm's token endpoint with the current
   K8s Secret value. A bad code with a *valid* client_secret returns
   400 ("invalid_grant"). A *wrong* client_secret returns 401.

   ```sh
   SEC=$(kubectl -n network get secret <client>-kanidm-oauth2-credentials \
     -o jsonpath='{.data.CLIENT_SECRET}' | base64 -d)
   curl -ks -o /dev/null -w "%{http_code}\n" \
     -X POST https://auth.home.kelch.io/oauth2/token \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -u "<client>:$SEC" \
     --data 'grant_type=authorization_code&code=fake&redirect_uri=https://<host>/oidc/callback'
   ```

   401 = drift confirmed. 400 = secrets agree; problem is elsewhere.

2. **Force kaniop to rotate.** Annotate the CR with
   `kaniop.rs/force-secret-rotation=true`. kaniop calls
   `idm_oauth2_rs_update(name, reset_secret=true)` on kanidm, re-reads
   `_basic_secret`, patches the K8s Secret, and auto-clears the
   annotation. The same reconcile sequence so the new secret is read
   from the same pod that just generated it — no second race window.

   ```sh
   kubectl -n network annotate kanidmoauth2client <client> \
     kaniop.rs/force-secret-rotation=true --overwrite
   ```

3. **Wait ~10s and re-probe.** Expected: secret value changes, HTTP
   returns 400, annotation cleared.

   ```sh
   sleep 10
   kubectl -n network get kanidmoauth2client <client> \
     -o jsonpath='{.metadata.annotations.kaniop\.rs/force-secret-rotation}'
   # repeat the curl probe from step 1 — expect 400
   ```

4. **Verify in browser.** Fresh-incognito visit to a gated host, log in
   via kanidm, complete the redirect. Should succeed without "Failed to
   exchange auth code".

## Notes

- The annotation is operational — do *not* commit it to Git. kaniop
  auto-clears it after a successful rotation; baking it into the
  manifest would re-trigger rotation on every Flux reconcile.
- stakater/reloader is set on traefik-admin for the credential Secrets,
  so kaniop's K8s Secret patch automatically rolls the traefik-admin
  pods. New pod env picks up the rotated value; the plugin re-reads at
  request time from process env.
- After rotation, any users currently authenticated against the
  affected client are unaffected (the rotation only changes the
  `client_secret`, not the session cookie's encryption key).
- Track the upstream gap at [pando85/kaniop#491](https://github.com/pando85/kaniop/issues/491)
  and the eventual-consistency caveat at the
  [kanidm replication docs](https://kanidm.github.io/kanidm/stable/repl/index.html).
