# *arr hardlink rework: single share-root mount + NFSv4 ACL isolation

**Status:** Implemented 2026-05-13. **Current state is in
`docs/runbooks/arr-suite-bootstrap.md` (Isolation model section).** The
prose below captures the original design intent; the implementation
diverged in several non-trivial ways once contact with `synoacltool` and
DSM's actual ACL behavior forced corrections. See "Outcomes" at the
bottom for the deltas.

**Supersedes:** the per-app `subPath` mount isolation choice in
`docs/plans/20260508-arr-suite-setup.md` (Phases 4–5) and the corresponding
runbook layout in `docs/runbooks/arr-suite-bootstrap.md`. Phase 0 (NAS users,
`media` group, directory tree, snapshots, NFS export) remains correct.

## Context

The original plan paired two goals that turned out to be structurally
incompatible on this storage substrate:

1. **Per-app `subPath` mounts** so each *arr only sees its own category dir
   plus `.downloads` (mount-level isolation).
2. **Hardlinks** from `.downloads` into the library category dir for atomic
   *arr imports (the Phase-1 smoketest validated this on a share-root mount).

The `nfs.csi.k8s.io` CSI driver implements `subPath` by mounting
`server:/share/<subPath>` as a **separate NFS mount per entry** rather than
bind-mounting from a single parent mount. Reproduced from a Radarr pod:

```
$ mount | grep /data
10.32.25.5:/volume1/media/movies     on /data/movies      type nfs4 ...
10.32.25.5:/volume1/media/.downloads on /data/.downloads  type nfs4 ...

$ ln /data/.downloads/usenet/movies/X.mkv /data/movies/Movie/X.mkv
ln: failed to create hard link ... : Cross-device link
```

Separate mounts ⇒ separate kernel superblocks ⇒ `link(2)` rejects with
`EXDEV` regardless of how the NFS server reports `fsid`. The Phase-1
smoketest passed because the smoketest pod mounts the share root once at
`/data` (`tools/media-nfs-smoke/smoketest.yaml`); that shape was never
extended to the *arr helmreleases. Sonarr/Radarr/Lidarr have been failing
hardlink imports in production since 2026-05-12.

A second, independent issue surfaced during diagnosis: SAB's `permissions`
config in `sabnzbd.ini` applies its numeric mode literally to directories
without OR'ing in execute bits. `permissions = 664` left newly-created job
folders at mode `664` (`rw-rw-r--`, no traverse), causing
`os.mkdir(...job/__ADMIN__)` to fail `EACCES`. Cleared via the SAB UI on
2026-05-12; the correct value is `775` (translates to dirs 775, files 664
via SAB's `removexbits` path — see `filesystem.py:670`). `664`/empty leave
files at mode 644, which then trips Linux/NFS `protected_hardlinks` (the
linker must own the file or have group-write) when *arr atomic-import
tries to `link()`.

## Decisions

| | Decision | Why |
|---|---|---|
| **Mount shape (*arr)** | Single share-root mount: `bjw-s globalMounts: [{ path: /data }]`, **no `subPath`**. Each *arr pod sees the entire `/volume1/media` tree as `/data/{tv,movies,music,books,audiobooks,.downloads}`. | Required for hardlinks: one kernel superblock = `link(2)` works across category dirs. Confirmed in survey of bjw-s-labs/home-ops, onedr0p/home-ops, joryirving/home-ops — universal pattern. |
| **Mount shape (downloaders + unpackerr)** | SAB keeps `subPath: .downloads/usenet`; qB keeps `subPath: .downloads/torrents`; unpackerr keeps `subPath: .downloads`. | Downloaders + unpackerr don't hardlink across categories — their narrower view is fine and provides extra blast-radius reduction. Hardlinks live entirely on the *arr side. |
| **Isolation mechanism** | NFSv4 ACLs on `/volume1/media/{tv,movies,music,.downloads}` — Synology DSM's "Synology ACL" is an implementation of the NFSv4 ACL standard (RFC 7530/8881). Per-folder Allow ACEs for each app, set via DSM File Station → Properties → Permission, with Apply To covering "This folder + All Descendents". | Mount-based isolation isn't an option (kills hardlinks). POSIX writer-groups would work but force "this folder has these rules" to be expressed via "this identity belongs to these groups" — indirect. NFSv4 ACLs map directly: per-folder ACE list. Fine-grained ops let Bazarr create + modify its own subtitle sidecars without granting it the ability to unlink media files. Portable to TrueNAS/ZFS (`acltype=nfsv4`) for future migration. |
| **SAB file permissions** | `permissions = 775` (set via SAB UI Config → Folders, captured declaratively where possible — see Open Questions). | Yields files at mode 664 via SAB's `removexbits` translation. Group-write on files satisfies `protected_hardlinks` so *arr can `link()` SAB-created files into the library. |
| **POSIX baseline (unchanged)** | `/volume1/media` subdirs stay `2775` with the existing `media` group (GID 65537) for: (a) `.downloads` shared-workspace writes, (b) Plex/Jellyfin reads via `other r-x` on dirs and `other r--` on files. | NFSv4 ACLs are additive over POSIX bits. The `media` group continues to serve as the cross-cutting read/handoff identity. Per-app UIDs (1036–1044) stay — used by per-folder ACEs as the principal name. |
| **Per-category writer groups** | **Not adopted.** Initially designed in chat; rejected once we settled on ACLs. Adding groups in parallel with ACLs is double-bookkeeping for one isolation property. | NFSv4 ACL grants are folder-scoped and named by user; no need for a group abstraction over them. Skip the `synogroup --add media-{tv,movies,...}` step. |
| **Readarr removal** | Books/audiobooks categories get **no writer ACE** in this rework; they stay readable via inherited share-root ACEs and unwritable until a replacement reader-arr lands. | Per operator note 2026-05-13: Readarr is unmaintained and broken. Removed in this changeset (originally planned as a follow-up; folded in pre-merge). |

## NAS-side ACL layout

Set on each folder via DSM File Station → right-click → Properties →
Permission → Create. DSM exposes ACL operations under three categories
(Administration / Read / Write); none of the named Windows-style presets
("Modify", "Full Control") are surfaced in the UI — every ACE is built
by checking individual operation boxes. We use three reusable shapes:

Each writer principal gets a **two-ACE pair** per category root, not a
single ACE. The pair separates "what you can do *to* the category root
itself" from "what you can do *inside* it" — necessary because under
NFSv4 ACL semantics (RFC 7530 §6.2.1.3.2), `delete` permission on a
folder is sufficient by itself for the server to permit `rmdir` of that
folder, regardless of parent-dir permissions. A single Manager ACE
applying to "This folder" with `Delete` checked would give Sonarr
server-side permission to delete `/tv` itself — blocked currently only
by Linux's client-side VFS check on the parent dir, which is a fragile
layer to rely on. The two-ACE pattern eliminates the server-side
permission entirely.

### Shape "Manager" — full lifecycle within a category, but cannot delete the category root

For: `sonarr` on `/tv`, `radarr` on `/movies`, `lidarr` on `/music`,
`sabnzbd` / `qbittorrent` / `unpackerr` on `/.downloads`.

**ACE A — on the category root itself (no delete on self):**

| Category | Box | Checked? |
|---|---|---|
| Administration | Change permissions | ☐ |
| Administration | Take ownership | ☐ |
| Read | Traverse folders/Execute files | ☑ |
| Read | List folders/Read data | ☑ |
| Read | Read attributes | ☑ |
| Read | Read extended attributes | ☑ |
| Read | Read permissions | ☑ |
| Write | Create files/Write data | ☑ |
| Write | Create folders/Append data | ☑ |
| Write | Write attributes | ☑ |
| Write | Write extended attributes | ☑ |
| Write | **Delete subfolders and files** | ☐ |
| Write | **Delete** | ☐ |

**Apply To**: ☑ This folder, ☐ Child Folders, ☐ Child Files, ☐ All Descendents.

This ACE lets *arr add new top-level entries directly under `/tv`
(e.g., `mkdir /tv/NewShow/`) but withholds the two delete bits so the
category root itself cannot be removed.

**ACE B — inherits to descendants, full Manager set:**

| Category | Box | Checked? |
|---|---|---|
| Administration | Change permissions | ☐ |
| Administration | Take ownership | ☐ |
| Read | Traverse folders/Execute files | ☑ |
| Read | List folders/Read data | ☑ |
| Read | Read attributes | ☑ |
| Read | Read extended attributes | ☑ |
| Read | Read permissions | ☑ |
| Write | Create files/Write data | ☑ |
| Write | Create folders/Append data | ☑ |
| Write | Write attributes | ☑ |
| Write | Write extended attributes | ☑ |
| Write | Delete subfolders and files | ☑ |
| Write | Delete | ☑ |

**Apply To**: ☐ This folder, ☑ Child Folders, ☑ Child Files, ☑ All Descendents.

"This folder" deliberately unchecked → this ACE doesn't apply to `/tv`
itself; it's inherited by every descendant (new shows, new seasons, new
episode files). Full lifecycle on everything below the category root.

### Shape "Sidecar Writer" — Bazarr on `/tv` and `/movies`

Same two-ACE pair structure as Manager, but ACE B omits `Delete
subfolders and files` so Bazarr can manage its own subtitle sidecars
without being able to recursively wipe a show/movie folder.

**ACE A — on the category root itself:**

Same checkbox set as Manager ACE A above (Read + Create files + Create
folders + Write attrs + Write xattr; no Delete, no Delete subfolders and
files; Apply To: This folder only).

**ACE B — inherits to descendants:**

| Category | Box | Checked? |
|---|---|---|
| Administration | Change permissions | ☐ |
| Administration | Take ownership | ☐ |
| Read | Traverse folders/Execute files | ☑ |
| Read | List folders/Read data | ☑ |
| Read | Read attributes | ☑ |
| Read | Read extended attributes | ☑ |
| Read | Read permissions | ☑ |
| Write | Create files/Write data | ☑ |
| Write | Create folders/Append data | ☑ |
| Write | Write attributes | ☑ |
| Write | Write extended attributes | ☑ |
| Write | **Delete subfolders and files** | ☐ |
| Write | Delete | ☑ |

**Apply To**: ☐ This folder, ☑ Child Folders, ☑ Child Files, ☑ All Descendents.

Effect: Bazarr can create `.srt` sidecars in any subdir of `/tv`, and
the inherited `Delete` on those files lets Bazarr replace/remove its own
sidecars. Bazarr cannot unlink `.mkv` files via `delete_child` on the
season folder (the bit is unchecked), and cannot delete `/tv` itself
(ACE A withholds it).

**Known residual exposure (Sidecar Writer only):** ACE B's `Delete` bit
inherits to *all* files created in `/tv` subdirs, not just `.srt` files
— NFSv4 ACL inheritance propagates by flag, not by file type. So a
file Sonarr drops into `/tv/Show/Season01/Episode.mkv` also inherits a
Bazarr `Delete` ACE, technically letting Bazarr unlink media files via
single-file `delete`. The threat model is "Bazarr code path
deliberately deletes media" — Bazarr has no such code path in its
production build, and a future bug would be the threat regardless of
this ACL. Documented as accepted residual, not blocking.

### Shape "Reader" — `media` group baseline

For: `media` on `/volume1/media` (share root, traverse-only), and on each
category dir so Plex/Jellyfin and cross-cutting reads keep working.

| Category | Box | Checked? |
|---|---|---|
| Administration | Change permissions | ☐ |
| Administration | Take ownership | ☐ |
| Read | Traverse folders/Execute files | ☑ |
| Read | List folders/Read data | ☑ |
| Read | Read attributes | ☑ |
| Read | Read extended attributes | ☑ |
| Read | Read permissions | ☑ |
| Write | (all unchecked) | ☐ |

**Apply To**: ☑ This folder, ☑ Child Folders, ☑ Child Files, ☑ All Descendents.

### ACEs to set, by folder

| Folder | ACE | Shape |
|---|---|---|
| `/volume1/media` | `media` (group) Allow | Reader |
| `/volume1/media/tv` | `sonarr` (user) Allow | Manager |
| `/volume1/media/tv` | `bazarr` (user) Allow | Sidecar Writer |
| `/volume1/media/tv` | `media` (group) Allow | Reader |
| `/volume1/media/movies` | `radarr` (user) Allow | Manager |
| `/volume1/media/movies` | `bazarr` (user) Allow | Sidecar Writer |
| `/volume1/media/movies` | `media` (group) Allow | Reader |
| `/volume1/media/music` | `lidarr` (user) Allow | Manager |
| `/volume1/media/music` | `media` (group) Allow | Reader |
| `/volume1/media/.downloads` | `sabnzbd` (user) Allow | Manager |
| `/volume1/media/.downloads` | `qbittorrent` (user) Allow | Manager |
| `/volume1/media/.downloads` | `unpackerr` (user) Allow | Manager |
| `/volume1/media/.downloads` | `media` (group) Allow | Reader |
| `/volume1/media/books` | (no writer ACEs) | — |
| `/volume1/media/audiobooks` | (no writer ACEs) | — |

Books/audiobooks stay at the share-default ACL (admin + media + Everyone
read) — effectively read-only library trees until a replacement
reader-arr lands.

**Negative side handled implicitly**: a Radarr write to `/tv` matches no
Allow ACE for `radarr` and falls through to the `media` group ACE
(Reader). No explicit `Deny` ACEs are needed — "no matching Allow" is the
gate. This is cleaner than Deny entries because Deny ACEs evaluate first
in NFSv4 ACL order and can produce confusing interactions if reordered.

## Manifest changes

### Helmrelease patches

Four files change. Each collapses two `advancedMounts` entries into one
`globalMounts` entry:

**`kubernetes/apps/media/sonarr/app/helmrelease.yaml`**, same shape for
`radarr/`, `lidarr/`, `bazarr/`:

```diff
   persistence:
     media:
       type: persistentVolumeClaim
       existingClaim: media-library
-      advancedMounts:
-        sonarr:
-          app:
-            - path: /data/tv
-              subPath: tv
-            - path: /data/.downloads
-              subPath: .downloads
+      globalMounts:
+        - path: /data
```

`globalMounts` (rather than `advancedMounts` with a single entry) because
all four are single-controller, single-container pods — no per-container
variation. Matches bjw-s upstream conventions.

**SAB:** no manifest change required; `permissions = 775` lives in SAB's
config PVC (`/config/sabnzbd.ini`). See Open Questions for declarative
capture.

### Plan/runbook updates (same PR)

- `docs/plans/20260508-arr-suite-setup.md`: add a pointer at Phases 4–5 to
  this plan, the way `20260509-kaniop-migration.md` is pointed at from
  Phase 0 step 5.
- `docs/runbooks/arr-suite-bootstrap.md`: rewrite the "per-app subPath +
  shared `media` group is the isolation model" section to describe the
  NFSv4 ACL layout above. Keep the UID/GID table. Add the DSM
  click-by-click sequence for setting each folder's ACEs.

## Execution sequence

Done end-to-end in a single operator session; manifests merge in the
middle, with Flux suspended on both ends.

1. **Spin down the `media` namespace** (durable pause — per the
   `flux suspend hr`/`kubectl scale` revert learning, suspend the KS too):

   ```bash
   kubectl get kustomization -n media -o name | \
     xargs -I {} kubectl patch {} -n media --type=merge -p '{"spec":{"suspend":true}}'
   kubectl get hr -n media -o name | \
     xargs -I {} kubectl patch {} -n media --type=merge -p '{"spec":{"suspend":true}}'
   kubectl scale -n media deploy --all --replicas=0
   kubectl get pods -n media  # expect zero
   ```

2. **Pre-flight: existing-file permission cleanup on the NAS** — files SAB
   wrote with the previous `permissions = empty` setting are at mode 644;
   no group-write, will fail `protected_hardlinks` later. One-shot fixup
   from a NAS SSH session as root:

   ```bash
   find /volume1/media/.downloads -type f ! -perm -g+w -exec chmod g+w {} +
   ```

3. **Probe DSM "Apply To" semantics on a throwaway test folder** —
   verify the assumption that DSM's four `Apply To` checkboxes map to
   NFSv4 inheritance flags the way the two-ACE Manager/Sidecar Writer
   shapes expect. **Do not skip; the entire isolation design depends on
   the mapping being what we assume.**

   **a. Create the test folder** (in a NAS SSH session as root):

   ```bash
   mkdir /volume1/media/_acl-probe
   chown root:media /volume1/media/_acl-probe
   chmod 2775 /volume1/media/_acl-probe
   ```

   **b. Pattern A — "This folder" only.** Via DSM File Station → right-click
   `_acl-probe` → Properties → Permission → Create:
   - User/Group: `sonarr`
   - Type: Allow
   - Apply To: ☑ This folder, ☐ Child Folders, ☐ Child Files, ☐ All Descendents
   - Permissions: check only `Read → List folders/Read data` (one easy-to-spot bit)
   - Save with "Apply to this folder, sub-folders and files" checked.

   Inspect the resulting ACE:
   ```bash
   # On the NAS:
   synoacltool -get /volume1/media/_acl-probe

   # From a debug pod with nfs4-acl-tools installed
   # (the same throwaway pod recipe as step 4 of this section, with
   #  /volume1/media mounted at /data):
   nfs4_getfacl /data/_acl-probe
   ```

   **Expected — Pattern A:** the sonarr ACE on `_acl-probe` itself shows
   *no* inheritance flags. In `nfs4_getfacl` notation, the flags slot
   between the type and the principal should be empty:
   ```
   A::sonarr@DOMAIN:r       ← empty between the colons; no f, d, or i
   ```
   Then create a child file and a child dir as root on the NAS and check
   them:
   ```bash
   touch /volume1/media/_acl-probe/childfile
   mkdir /volume1/media/_acl-probe/childdir

   nfs4_getfacl /data/_acl-probe/childfile
   nfs4_getfacl /data/_acl-probe/childdir
   ```
   **Expected — Pattern A children:** *no* sonarr ACE on either child.
   The ACE we set was "This folder only" and should not propagate.

   **c. Pattern B — descendants only.** Delete the Pattern A ACE in DSM.
   Recreate as:
   - User/Group: `sonarr`
   - Type: Allow
   - Apply To: ☐ This folder, ☑ Child Folders, ☑ Child Files, ☑ All Descendents
   - Permissions: check only `Read → List folders/Read data` again
   - Save with "Apply to this folder, sub-folders and files" checked.

   Re-inspect:
   ```bash
   synoacltool -get /volume1/media/_acl-probe
   nfs4_getfacl /data/_acl-probe
   ```
   **Expected — Pattern B:** the sonarr ACE on `_acl-probe` itself shows
   inheritance flags including `i` (inherit_only):
   ```
   A:fdi:sonarr@DOMAIN:r    ← f=file_inherit, d=dir_inherit, i=inherit_only
   ```
   The `i` flag is the load-bearing part — it means the ACE is *stored*
   on the parent but does *not* apply to operations on the parent itself.

   Create fresh children (delete the previous ones first) and check:
   ```bash
   rm -rf /volume1/media/_acl-probe/{childfile,childdir}
   touch /volume1/media/_acl-probe/childfile
   mkdir /volume1/media/_acl-probe/childdir

   nfs4_getfacl /data/_acl-probe/childfile
   nfs4_getfacl /data/_acl-probe/childdir
   ```
   **Expected — Pattern B children:** both children have an inherited
   sonarr ACE *without* the `i` flag (inherit_only is dropped on
   inheritance — the inherited copy applies to the child itself):
   ```
   A:fd:sonarr@DOMAIN:r     ← f and d preserved, i dropped
   ```
   For the file child, `f`/`d` are semantically moot (files have no
   children), but they may still appear in the flag slot.

   **If either pattern doesn't match expectations**, stop and resolve
   before continuing. Possible deviations and what to do:
   - If "This folder" alone produces an ACE *with* `f`/`d` flags →
     DSM is silently adding inheritance even when "Child Folders" /
     "Child Files" are unchecked. Try unchecking everything except
     "This folder" via the `synoacltool -add` CLI instead of the UI
     (which lets you specify inheritance flags explicitly).
   - If Pattern B's ACE on `_acl-probe` lacks the `i` flag → the ACE
     applies to `_acl-probe` itself, which would defeat the "Sonarr
     can't delete /tv" protection. Same `synoacltool -add` fallback.
   - If `nfs4_getfacl` shows `nobody@DOMAIN` instead of `sonarr@DOMAIN`
     → NFSv4 idmap issue (Footgun probe 5); resolve before continuing.

   **d. Cleanup:**
   ```bash
   rm -rf /volume1/media/_acl-probe
   ```

   Document the observed flag mappings in
   `docs/runbooks/arr-suite-bootstrap.md` so future operators don't have
   to re-derive them.

   **Also capture the `synoacltool` invocation format** for use in step
   4. Run `synoacltool -h` and `synoacltool -get` against the test ACE
   to learn the exact letter codes for permission bits and inherit
   flags this DSM version emits. Note the captured format inline in
   `scripts/synology/apply-media-acls.sh` (see step 4).

4. **Apply NFSv4 ACLs via `scripts/synology/apply-media-acls.sh`**, run from a NAS
   SSH session as root. The script captures every ACE from the "ACEs to
   set, by folder" table declaratively and applies them via
   `synoacltool`. Source-of-truth lives in git, not in the DSM UI.

   ```bash
   # Copy the script to the NAS and run it:
   scp scripts/synology/apply-media-acls.sh admin@nas:/tmp/
   ssh admin@nas
   sudo -i
   /tmp/apply-media-acls.sh
   ```

   The script is idempotent (re-running produces the same state) and
   writes a timestamped backup of the prior ACL state to
   `/var/log/media-acls-backup-*.txt` before mutating anything. Manual
   UI fallback exists for emergency single-ACE tweaks but should not be
   the primary path — drift between UI state and script-recorded state
   is the failure mode we're avoiding.

5. **Verify ACLs were applied** from a NAS shell:

   ```bash
   # Synology native:
   synoacltool -get /volume1/media/tv
   synoacltool -get /volume1/media/movies
   # ... etc

   # Or from a cluster pod (nfs4-acl-tools required):
   kubectl -n media run aclcheck --rm -it --image=ubuntu --restart=Never \
     --overrides='{"spec":{"volumes":[{"name":"m","persistentVolumeClaim":{"claimName":"media-library"}}],"containers":[{"name":"x","image":"ubuntu","stdin":true,"tty":true,"volumeMounts":[{"name":"m","mountPath":"/data"}]}]}}' \
     -- bash -c 'apt-get update && apt-get install -y nfs4-acl-tools && nfs4_getfacl /data/tv'
   ```

   Expected: per-folder ACEs match the table; principals resolve to named
   users (not `nobody`).

5. **Merge the helmrelease patches** (this plan doc + the four
   `globalMounts` changes) to `main`. Wait for Flux source-controller to
   fetch (or `flux reconcile source git flux-system` from kubectl).

6. **Resume the namespace** in this order:

   ```bash
   # Resume KS first so updated manifests propagate into HR specs
   kubectl get kustomization -n media -o name | \
     xargs -I {} kubectl patch {} -n media --type=merge -p '{"spec":{"suspend":false}}'
   # Then resume HR so helm-controller upgrades with new values
   kubectl get hr -n media -o name | \
     xargs -I {} kubectl patch {} -n media --type=merge -p '{"spec":{"suspend":false}}'
   ```

   Helm-controller restores `replicas: 1` on the next reconcile (≤1m);
   no manual scale-up needed.

7. **Validate** — see next section.

## Verification + footguns

Run each probe; do not declare success until every one passes.

**1. Hardlinks work across category dirs.** From a Radarr pod:

```bash
SRC=/data/.downloads/usenet/movies/<some-existing-file>.mkv
ln "$SRC" /data/movies/probe-hardlink && \
  stat -c "links=%h inode=%i" /data/movies/probe-hardlink && \
  rm /data/movies/probe-hardlink
# Expect: links=2, no errors
```

**2. ACL denies cross-category writes.** From a Radarr pod:

```bash
touch /data/tv/probe-wrong-write 2>&1
# Expect: Permission denied (Radarr is not in any /tv writer ACE)
```

**2b. ACL prevents *arr from deleting its own category root.** From a
Sonarr pod (test the two-ACE pattern's protection):

```bash
# Sonarr SHOULD be able to create a top-level entry under /tv:
mkdir /data/tv/probe-self-mkdir && rmdir /data/tv/probe-self-mkdir
# Sonarr SHOULD NOT be able to remove /tv itself:
rmdir /data/tv 2>&1
# Expect: Permission denied (or rejected by client VFS — either is fine,
# but the server-side ACL must independently reject it)
```

Repeat the second variant from each *arr against its own category root.

**3. SAB chmod doesn't clobber ACLs on Synology.** Trigger a small
download, then check the resulting file's ACL:

```bash
nfs4_getfacl /data/.downloads/usenet/<job>/file.ext
# Expect: explicit sabnzbd/qbittorrent/unpackerr/media ACEs survive
#         alongside the synthesized owner@/group@/everyone@ entries.
# If clobbered: see Open Questions #2.
```

**4. `fsGroupPolicy: None` on the NFS CSI driver.** Catastrophic if not:
kubelet would chown the entire shared NFS tree per pod mount.

```bash
kubectl get csidriver nfs.csi.k8s.io -o jsonpath='{.spec.fsGroupPolicy}'
# Expect: "None"
```

**5. NFSv4 idmap resolves UIDs to names.** If pods see `nobody:nobody`,
per-UID ACE matching can't work.

```bash
kubectl -n media exec deploy/radarr -- stat -c '%U:%G' /data/movies
# Expect: radarr:media (not nobody:nobody)
```

**6. `protected_hardlinks` no longer blocks.** Already covered by probe
1, but specifically: SAB-created files must have group-write (mode
664). If `permissions = 775` is not set in `sabnzbd.ini`:

```bash
kubectl -n media exec deploy/sabnzbd -- \
  grep '^permissions' /config/sabnzbd.ini
# Expect: permissions = 775
```

**7. Bazarr is in sidecar (not embed) mode.** Embed mode would need
write_data on `.mkv` files, which the ACL blocks. Verify in Bazarr UI:
Settings → Subtitles → "Subtitle Folder" = `Current` or `Alongside Media`
(not embed). Document as a deliberate trade-off.

**8. Existing-file ACL migration.** "Apply to this folder, sub-folders
and files" rewrites every child's ACL. Confirm no manually-tuned
permissions exist beforehand:

```bash
# On the NAS:
find /volume1/media -type f -newer /volume1/media -ls | head
# Eyeball for unexpected ownership/permissions before applying.
```

**9. *arr operational sanity** — drop one test NZB into SAB pointed at
the tv category, watch it complete and import to /tv via Sonarr. Then
trigger an upgrade (force-search a higher-quality version of an existing
episode). Both the import (link to /tv) and the upgrade (delete old +
link new) must succeed. This is the end-to-end functional test.

## Affected files

**Update**:
- `kubernetes/apps/media/sonarr/app/helmrelease.yaml` — collapse `advancedMounts` → `globalMounts: [{ path: /data }]`.
- `kubernetes/apps/media/radarr/app/helmrelease.yaml` — same.
- `kubernetes/apps/media/lidarr/app/helmrelease.yaml` — same.
- `kubernetes/apps/media/bazarr/app/helmrelease.yaml` — same.
- `docs/plans/20260508-arr-suite-setup.md` — pointer to this plan at Phases 4–5.
- `docs/runbooks/arr-suite-bootstrap.md` — replace subPath-isolation section with NFSv4 ACL layout + DSM click sequence.

**Add**:
- `docs/plans/20260513-arr-hardlink-rework.md` — this doc.

**Do not change** (intentionally — these are correct as-is):
- `kubernetes/apps/media/sabnzbd/app/helmrelease.yaml` — keeps `subPath: .downloads/usenet`.
- `kubernetes/apps/media/qbittorrent/app/helmrelease.yaml` — keeps `subPath: .downloads/torrents`.
- `kubernetes/apps/media/unpackerr/app/helmrelease.yaml` — keeps `subPath: .downloads`.
- `kubernetes/apps/media/storage/` — PV/PVC unchanged; share-root mount with `volumeHandle: media-library`.

## Open questions

1. **Declarative SAB `permissions = 775` capture.** The setting currently
   lives in `/config/sabnzbd.ini` on the config PVC. The home-operations
   image supports `SABNZBD__<KEY>` env-var injection that rewrites the
   ini before SAB starts (proven by `SABNZBD__HOST_WHITELIST_ENTRIES` in
   the current helmrelease). Need to verify whether `SABNZBD__PERMISSIONS:
   "775"` maps to the `permissions` key. If yes, add to helmrelease in
   this PR for declarative reproducibility on config-PVC restore. If no,
   document the manual UI step in the runbook and leave it as
   bootstrap-time operator work. Validate by deleting the line from
   sabnzbd.ini in a debug pod, restarting SAB, and checking whether the
   env var re-populates it.

2. **Synology chmod-on-ACL behavior.** If verification probe 3 shows ACLs
   get clobbered by SAB's chmod, options are: (a) accept POSIX-only on
   SAB-written files (parent dir ACLs still provide most of the isolation
   — see Footgun discussion in chat 2026-05-13), or (b) use a SAB env var
   to skip the chmod and set `UMASK=002` on the SAB container. (a) is
   probably fine; (b) preserves the explicit ACEs on files but may not be
   supported by the home-operations image. Resolve empirically post-merge.

3. **NFS CSI driver `fsGroupPolicy`.** Verify probe 4 result. If not
   `None`, separate PR to set it on the CSIDriver object — blocking
   prerequisite for any pod using the shared NFS volume safely. Likely
   already correct but unverified.

4. **DSM Configuration Backup coverage of folder-level ACLs.** Synology's
   documentation is unclear on whether `synoacltool` xattrs are captured
   in the Configuration Backup tool (which definitely captures
   share-level ACLs). Validate by exporting a backup post-rework and
   grepping for `sonarr` in the unpacked archive. If folder-level ACLs
   aren't included, add a periodic `nfs4_getfacl -R /volume1/media >
   acl-backup.txt` step to the runbook with the output committed
   somewhere durable.

5. **`nfs4-acl-tools` availability in *arr containers.** The
   home-operations images don't include `nfs4_getfacl` by default. Probe
   2 (cross-category write denial) works without it. Probes that need
   ACL inspection are run from a debug pod with the tool installed
   (see step 4 of execution sequence). No change to the *arr images
   needed.

## Outcomes (2026-05-13)

The deployed ACL layout diverges from the original design above in
several places. Each divergence was forced by an empirical finding
during execution; reasoning kept here for the next time someone hits
the same wall.

1. **Two-ACE pair → single ACE per writer.** The plan called for ACE A
   (this-folder, no `d`) + ACE B (descendants-only, full Manager) per
   writer to prevent the writer from `rmdir`'ing the category root.
   Implementation collapsed to a single ACE with perms `rwxp-DaARWc--`
   (note absent `d`) and inherit `fd--`. Same protection — without `d`
   on `/tv` itself and without `D` on `/volume1/media`, sonarr can't
   delete `/tv` — but expressed as one ACE rather than two, which reads
   cleaner in `synoacltool -get` and DSM Properties.

2. **"Sidecar Writer" shape dropped.** Bazarr now gets full Manager on
   `/tv` and `/movies` rather than a Manager-minus-`D` "Sidecar"
   variant. The Sidecar distinction was theoretical isolation against
   "Bazarr code deliberately unlinks media files" — Bazarr has no such
   code path; the threat model didn't justify a second permission
   shape.

3. **`media` group repurposed as the writer-side principal.** Original
   design used `media` as the universal Reader group, with Jellyfin a
   member. That conflated producer and consumer roles: any Deny on
   `media` to carve out Jellyfin would deny *arr writers too, since
   Synology canonicalizes Deny-first regardless of insertion order.
   Resolution: Jellyfin removed from `media` group membership on the
   NAS; `media` now represents writers only (sonarr, radarr, lidarr,
   bazarr, sabnzbd, qbittorrent, unpackerr). Jellyfin gets a per-user
   `Allow Reader` ACE on the share root with `fd--` inheritance, and
   a per-user `Deny DENY_ALL` on `/.downloads`.

4. **`media` Reader on share root uses `---n`, not `fd--`.** Original
   design propagated `media:Reader` to every category. That was
   unnecessary (per-app Manager ACEs handle writer access; reads via
   group are not a goal) and caused doubled inherited entries on
   children. Switched to `---n` (this folder only, no propagation) —
   *arr/downloaders still get traverse on the share root so they can
   reach their category dirs.

5. **No `media:Reader` on category folders.** Drops out of #3 + #4 —
   the only library reader is Jellyfin (covered by the per-user ACE
   inherited from share root), and *arr apps reach their categories via
   their own per-user Manager ACE.

6. **`/.downloads` has explicit Jellyfin Deny instead of Plan-style
   `media` Reader.** Original design: `media:Reader` on `/.downloads`
   so library readers could see download progress. Reality: we *don't*
   want Jellyfin to scan in-flight downloads. New design uses
   `user:jellyfin Deny DENY_ALL fd--` on `/.downloads`; Synology
   canonicalizes Deny to position 0 in the ACL, so it evaluates before
   any inherited Allow.

7. **`administrators` group ACE added on share root.** Discovered during
   testing: attaching a Synology ACL to a folder disables the implicit
   SMB-admin bypass that operates on Linux-mode folders. `kelchm` lost
   browse access to the `media` share until we added
   `group:administrators Allow Manager fd--` on `/volume1/media`. The
   ACE inherits everywhere, restoring DSM/SMB admin access without
   per-folder grants.

8. **`synoacltool -enforce-inherit` is destructive in the wrong
   direction.** Despite the name, it pulls parent's inheritable ACL
   *onto* the path, overwriting the path's own level:0 ACEs. We removed
   this call from the apply script; it was wiping the per-app writer
   ACEs we'd just added.

9. **`synoacltool -set-archive PATH is_inherit` is needed after `-del`
   + `-add`.** `-del` clears the `is_inherit` archive bit. Without it,
   children don't pull in the parent's new inherited ACEs even when
   they have an ACL. Apply script calls `-set-archive` on each managed
   path after the `-add` loop.

10. **No descendant migration via `synoacltool`.** `-set-archive
    is_inherit` works on entries already in ACL mode, but cannot
    promote a Linux-mode entry into ACL mode (errors with "Not allowed
    to do it"). DSM's UI "Apply to this folder, sub-folders and files"
    checkbox uses a code path synoacltool doesn't expose. So
    pre-rework Linux-mode descendants of `/.downloads` stay
    Linux-mode. This is not a security issue because the
    `/.downloads` parent ACL gates access — Jellyfin is denied at
    `/.downloads` itself, so descendants' mode doesn't matter.

11. **POSIX `2775` on category roots renders as `2550` once ACL is
    attached.** Synology back-derives the POSIX mode from the ACL when
    one is present — owner/group bits collapse since access goes
    through named ACL principals. The setgid bit remains. No
    operational impact; just visually surprising on `ls -l`.

12. **DSM-level `everyone::Reader` propagates to all category folders
    at level:2.** DSM applies a baseline ACL on `/volume1` with `root`
    and `everyone` Allow ACEs that inherit into the share. The
    `everyone::Reader` entry grants read+traverse to every authenticated
    NFS principal — functionally equivalent to the pre-rework POSIX
    `other r-x` on mode 2775. Write isolation is unaffected; read
    breadth is constrained at the NFS-export layer (three node IPs).
