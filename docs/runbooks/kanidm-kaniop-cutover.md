# Kanidm cutover: hand-rolled StatefulSet → kaniop operator

One-time runbook for PR B of the Kanidm pivot
(`docs/plans/20260509-kaniop-migration.md`). Combines two relocations:

1. **Operator move**: kaniop pod from `kaniop/` namespace → `identity/` namespace.
   Pure config relocation, no PVC, no state.
2. **Workload cutover**: hand-rolled `kanidm` StatefulSet in `auth/` namespace →
   operator-managed Kanidm CR in `identity/` namespace. **Destructive** — wipes
   the Kanidm data PVC.

The DB is empty at the time of cutover (only `idm_admin` recovered, no `kelchm`,
no OAuth2 clients) — preserving its state would not save any work that hasn't
been re-encoded as declarative CRs in PR B itself.

## Pre-flight checklist

Run these checks **before** opening the merge button on PR B. If any fail, stop and
resolve before continuing.

- [ ] **Current operator location confirmed.** PR A merged kaniop into the `kaniop`
  namespace; PR B will dissolve that placement. Confirm the pod is reachable so we
  can recognize the eventual relocation:
  ```sh
  kubectl get pods -n kaniop
  # Expect: kaniop-* pod Running
  kubectl get crds | grep kaniop.rs
  # Expect: kanidms.kaniop.rs, kanidmgroups.kaniop.rs,
  #         kanidmpersonsaccounts.kaniop.rs, kanidmoauth2clients.kaniop.rs,
  #         kanidmserviceaccounts.kaniop.rs
  ```
  CRDs are cluster-scoped and survive the namespace move; only the pod relocates.
- [ ] **DB is empty enough.** This procedure deletes the PVC. Any data only in
  the live Kanidm (not in PRs B/C as declarative CRs) will be lost.
  ```sh
  kubectl -n auth exec sts/kanidm -- kanidmd domain show 2>/dev/null || true
  # If you see anything beyond the auto-recovered idm_admin and the empty
  # default state, STOP and reconsider — declare the resource in a CR first.
  ```
- [ ] **Cert is intact.** The cert-manager Certificate moves with the workload from
  `auth/` to `identity/`. Verify the existing TLS Secret is healthy (it'll be
  re-issued in the new namespace, but the prior issuance proves the DNS01 flow
  works):
  ```sh
  kubectl -n auth get secret auth-home-kelch-io-tls
  # Expect: present, type kubernetes.io/tls, age >= 1d
  ```

## Cutover sequence

### 1. Suspend Flux on both Kustomizations

Stops the reconciler from fighting us during the StatefulSet teardown and prevents
the pruner from removing things in the wrong order.

```sh
flux suspend kustomization kaniop -n flux-system
flux suspend kustomization kanidm -n flux-system
```

### 2. Scale down the hand-rolled StatefulSet and delete its PVC

```sh
kubectl -n auth scale statefulset kanidm --replicas=0
kubectl -n auth wait --for=delete pod/kanidm-0 --timeout=120s
kubectl -n auth delete pvc data-kanidm-0
```

The PVC delete blocks until Longhorn finalizes; with no live attacher this typically
completes in <30s. If it hangs, check that the StatefulSet pod is truly gone
(`kubectl -n auth get pods`) and that no debug pod has the PVC mounted.

### 3. Merge PR B

Merge via the GitHub UI. PR B:

- Deletes `kubernetes/apps/kaniop/` (PR A's namespace placement).
- Deletes `kubernetes/apps/auth/` (the hand-rolled placement).
- Adds `kubernetes/apps/identity/` with kaniop and kanidm as siblings.

### 4. Resume Flux and let it reconcile

```sh
flux reconcile kustomization cluster-apps -n flux-system --with-source
```

The cluster-apps aggregator (which has `prune: true`) will:

1. Detect that the `kaniop` and `auth-kanidm` Kustomizations no longer exist in git
   and prune them — including the kaniop pod in `kaniop/` namespace.
2. Apply the new `identity` Kustomization, which creates the namespace, installs
   kaniop, then (via dependency ordering) provisions the Kanidm CR.

### 5. Verify operator relocation

```sh
kubectl get pods -n identity -l app.kubernetes.io/name=kaniop
# Expect: kaniop pod Running in identity namespace
kubectl get ns kaniop
# Expect: NotFound (or Terminating)
```

If the old `kaniop` namespace lingers in Terminating, check for finalizers on its
contents — typically self-resolves in <60s.

### 6. Wait for the operator to bring up the new Kanidm pod

```sh
kubectl -n identity get kanidm kanidm -w
# Wait until status conditions show Available=True (typically 60–120s).
# Ctrl-C once Ready.

kubectl -n identity get pods -l app.kubernetes.io/name=kanidm
# Expect: kanidm-default-0 Running, 1/1 Ready
```

### 7. Verify the operator created its admin Secret

kaniop runs `kanidmd recover-account` for both `admin` and `idm_admin` on first
reconcile and stores the generated passwords in a Secret:

```sh
kubectl -n identity get secret kanidm-admin-passwords
# Expect: present, with keys ADMIN_PASSWORD, IDM_ADMIN_PASSWORD,
#         ADMIN_USERNAME, IDM_ADMIN_USERNAME
```

Retrieve `idm_admin`'s password (the one you'll use to log in to the web UI):

```sh
kubectl -n identity get secret kanidm-admin-passwords \
  -o jsonpath='{.data.IDM_ADMIN_PASSWORD}' | base64 -d
echo   # trailing newline
```

Save this to your password manager immediately; the password rotates if you delete
the Secret, but the in-Kanidm credential rotates separately and the two will
diverge.

### 8. Sanity-check the web UI

In a browser, hit <https://auth.home.kelch.io>. Log in as `idm_admin` with the
password from step 7.

- [ ] Login completes (no TLS errors, no proxy 502s).
- [ ] Web UI loads to the dashboard.
- [ ] **Groups → list** shows `arr-admins` (reconciled by kaniop from
      `group-arr-admins.yaml` within ~60s of pod ready).
- [ ] No replication topology configured (single-replica deployment).

### 9. Verify the operator is healthy long-term

```sh
kubectl -n identity logs deploy/kaniop --tail=50
# Expect: no repeated reconcile errors. Periodic "Reconcile completed" messages
# for the kanidm cluster + arr-admins group are normal.
```

## Validation

Run the test-plan items in PR B's description before declaring the cutover done.
PR C (NetworkPolicy + backups + kelchm) follows immediately.

## Rollback

PR B is the kind of PR you don't roll back — by the time something would push you
to revert, the old PVC is gone and the old image is potentially gone too (deleted
by Spegel or kubelet GC after the StatefulSet was removed).

If the operator-managed pod fails to come up:

1. Check operator logs (`kubectl -n identity logs deploy/kaniop --tail=200`) for
   reconcile errors. Common culprits: TLS Secret missing
   (`auth-home-kelch-io-tls`), Longhorn unable to provision new PVC, image pull
   failure on `kanidm/server:1.9.2`.
2. Resolve in-place (fix the Kanidm CR, push a follow-up commit) rather than
   reverting. The DB is empty, so any "fix forward" preserves zero state.
3. **Only** if everything is broken and we need to back out, revert PRs A + B
   together and re-deploy the hand-rolled YAML from the pre-pivot commit
   (`c4856a0`). The hand-rolled deployment had no upstream dependencies.

## What this runbook does NOT cover

- **PR C (hardening)**: NetworkPolicy, `KANIDM_TRUST_X_FORWARD_FOR=true`,
  `KANIDM_ONLINE_BACKUP_*` env vars, `KanidmPersonAccount kelchm`. Lands as its
  own PR; see `docs/plans/20260509-kaniop-migration.md`.
- **Restore drill**: separate runbook (`docs/runbooks/kanidm-restore.md`)
  written as part of PR C.
- **Scale-out to 3 replicas**: PR E. After PR B + PR C are stable, bump
  `replicaGroups[0].replicas` from 1 to 3; the operator handles peer cert
  exchange.
