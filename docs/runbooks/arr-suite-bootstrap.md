# *arr Suite Bootstrap

Captured state from bootstrapping the media stack described in `docs/plans/20260508-arr-suite-setup.md`. Pinned values here are the source of truth for per-app `runAsUser` / `runAsGroup` in HelmReleases.

## Synology (Athena)

- DSM 7.3.2 (build 86009)
- Host: `Athena`
- Service users created via `synouser --add` on 2026-05-11; primary group is DSM-default `users` (gid=100). Pod-side `runAsGroup: 65537` overrides primary group at the container level; the setgid bit on `/volume1/media` directories forces `media` group ownership on new files regardless of writer.
- Shell on all service users: `/sbin/nologin` (DSM default for `synouser --add`).

### Pinned UIDs and GIDs

| App         | UID  | Library access                    |
|-------------|------|-----------------------------------|
| qbittorrent | 1036 | .downloads RW                      |
| sabnzbd     | 1037 | .downloads/usenet RW               |
| sonarr      | 1038 | tv RW + .downloads RW              |
| radarr      | 1039 | movies RW + .downloads RW          |
| lidarr      | 1040 | music RW + .downloads RW           |
| bazarr      | 1042 | tv/movies RW                      |
| unpackerr   | 1043 | .downloads RW (extract in-place)   |
| jellyfin    | 1044 | library RO + /dev/dri host group  |

| Group | GID   |
|-------|-------|
| media | 65537 |

Apps without a Synology user (never touch NFS, API-only over the cluster network): Prowlarr, Flaresolverr, Recyclarr, request UI (Jellyseerr/Seerr).

## Share filesystem state

`/volume1/media` on Btrfs (`Volume 1`). Created via DSM UI as shared folder `media` with "No access" for all DSM users (NFS-only share — no DSM/SMB consumers). 2026-05-11 bootstrap output:

```
/volume1/media                                           drwxrwsr-x root:media
/volume1/media/{tv,movies,music,books,audiobooks}        drwxrwsr-x root:media
/volume1/media/.downloads                                 drwxrwsr-x root:media
/volume1/media/.downloads/manual                          drwxrwsr-x root:media
/volume1/media/.downloads/{torrents,usenet}               drwxrwsr-x root:media
/volume1/media/.downloads/{torrents,usenet}/.incomplete   drwxrwsr-x root:media
/volume1/media/.downloads/{torrents,usenet}/{tv,movies,music,books}
                                                         drwxrwsr-x root:media
/volume1/media/@eaDir                                    drwxrwxrwx+ root:root  (DSM-managed)
```

Setgid (`s`) on all directories we created: new files inherit `gid=media` regardless of writer's primary group, which makes cross-app hardlinks work without negotiating per-app group ownership.

## Access paths

Two access paths to `/volume1/media`, by design:

- **NFS** — cluster apps mount via csi-driver-nfs (see PV `media-library` at `kubernetes/apps/media/storage/`). Per-app UIDs 1036–1044 enforced via pod `runAsUser`; `Map root to guest` neuters any UID-0 client. This is the primary access path; all *arr / downloader / Jellyfin writes flow this way.
- **SMB** — operator account `kelchm` (UID 1026) RW for ad-hoc housekeeping; guest RO so anyone on Main VLAN can browse the library without auth. SMB writes inherit `media` group via the directory setgid bit, so files remain readable by NFS-side apps. Guest RO is consistent with the existing POSIX setup (`chmod 2775` already gives `other` r-x). `.DS_Store` artifacts get ignored by *arr default patterns.

## Isolation model

Two layers, with NFSv4 ACL authoritative when present:

- **POSIX baseline.** `/volume1/media` and its category subdirs were bootstrapped at `2775 root:media`. Once a Synology ACL is attached (as the apply script does), ACL governs access decisions and the POSIX mode shown by `ls -l` is synthesized from the ACL state (the bare folders typically render as `2550 dr-xr-s---`). Setgid still applies — new files inherit `gid=media` regardless of writer.
- **NFSv4 ACLs.** Per-folder Allow ACEs grant writes; a per-user Deny on `/.downloads` carves out Jellyfin. *arr Helmreleases mount the share root once at `/data` (no `subPath`) so `link(2)` works across category dirs for atomic imports. Downloaders and unpackerr keep their own `subPath` mounts for blast-radius reduction.

### ACL layout

As applied by `scripts/synology/apply-media-acls.sh`:

| Folder | Principal | Action | Perms | Inherit |
|---|---|---|---|---|
| `/volume1/media` | `administrators` (group) | Allow | Manager | `fd--` |
| `/volume1/media` | `media` (group) | Allow | Reader | `---n` |
| `/volume1/media` | `jellyfin` (user) | Allow | Reader | `fd--` |
| `/volume1/media/tv` | `sonarr` (user) | Allow | Manager | `fd--` |
| `/volume1/media/tv` | `bazarr` (user) | Allow | Manager | `fd--` |
| `/volume1/media/movies` | `radarr` (user) | Allow | Manager | `fd--` |
| `/volume1/media/movies` | `bazarr` (user) | Allow | Manager | `fd--` |
| `/volume1/media/music` | `lidarr` (user) | Allow | Manager | `fd--` |
| `/volume1/media/.downloads` | `media` (group) | Allow | Manager | `fd--` |
| `/volume1/media/.downloads` | `jellyfin` (user) | Deny | DENY_ALL | `fd--` |
| `/volume1/media/books`, `/audiobooks` | — | — | — | (no level:0; inherit-only) |

**Permission shapes** (13-char positional slot `r-w-x-p-d-D-a-A-R-W-c-C-o`):

| Shape | Slot | Notes |
|---|---|---|
| Manager | `rwxp-DaARWc--` | Full lifecycle minus `d` (delete-self). `D` (delete-child) allows recursive removal of contents; `d` absent prevents `rmdir` of the category root itself. |
| Reader | `r-x---a-R-c--` | Read + traverse + read-attrs/xattr/acl. |
| DENY_ALL | `rwxpdDaARWcCo` | Every NFSv4 bit set. Used only for the Jellyfin Deny on `/.downloads`. |

**Inherit-flag shapes** (4-char positional slot `f-d-i-n`):

| Shape | Slot | Notes |
|---|---|---|
| All | `fd--` | This folder + every descendant (files and subdirs). |
| Local | `---n` | This folder only, no propagation. |

### Read access and the `---n` on `media`

`media` group has `Reader / ---n` on the share root so its members (the *arr/downloaders) can traverse `/volume1/media` to reach their category folder — but the entry does **not** propagate. Per-app read inside a category goes through that app's own per-user Manager ACE; cross-category reads via group membership are intentionally not granted.

### Jellyfin Deny on `/.downloads`

Jellyfin is the only library reader today; it gets Reader on every category via `user:jellyfin Allow Reader fd--` inherited from the share root. The same inheritance would propagate Reader into `/.downloads`, which we don't want — Jellyfin shouldn't scan in-flight downloads. The explicit `user:jellyfin Deny DENY_ALL fd--` on `/.downloads` overrides: Synology's `synoacltool` canonicalizes Deny ACEs to the top of the ACL on store, so Deny evaluates before any inherited Allow.

When a second library reader appears (Plex, Emby, …), it gets its own per-user Deny on `/.downloads`. At three or more readers, promote to a `library-readers` group.

### Level:2 entries inherited from `/volume1`

DSM maintains a baseline ACL on the volume root (`/volume1`) with `user:root`, `group:root`, and `everyone::` Allow ACEs. These propagate down to `/volume1/media` (level:1) and into the category folders (level:2):

- `user:root:Manager fd--` — matches actual UID 0 only. NFS `root_squash` maps cluster UID 0 to `anonuid=1025`, so cluster pods never match this.
- `group:root:Reader fd--` — DSM root group; cluster pods aren't members.
- `everyone::Reader fd--` — matches every authenticated NFS principal, so any cluster pod can read. Functionally equivalent to the pre-rework POSIX `other r-x`. Write isolation is unaffected (this is read-only). NFS export restrictions (three node IPs) constrain reachability at a layer above.

### Inspecting ACL state

```bash
synoacltool -get /volume1/media/<folder>      # on the NAS
nfs4_getfacl /data/<folder>                   # from a cluster pod with nfs4-acl-tools
```

The apply script is the source of truth. Re-runs are idempotent (resets each folder's level:0 ACEs, re-adds, re-enables `is_inherit` archive bit).

### DSM Properties → Permission (manual inspection)

For ad-hoc inspection or one-off manual ACE edits, DSM's File Station → right-click folder → Properties → Permission shows the same ACL with NFSv4 perm bits surfaced as named checkboxes:

| Bit | DSM checkbox |
|---|---|
| `r` | Read → List folders/Read data |
| `w` | Write → Create files/Write data |
| `x` | Read → Traverse folders/Execute files |
| `p` | Write → Create folders/Append data |
| `d` | Write → Delete |
| `D` | Write → Delete subfolders and files |
| `a` / `A` | Read/Write → (Read/Write) attributes |
| `R` / `W` | Read/Write → (Read/Write) extended attributes |
| `c` / `C` | Read → Read permissions / Administration → Change permissions |
| `o` | Administration → Take ownership |

`Apply To` checkboxes correspond to the inherit slot: `This folder` toggles whether the ACE applies here (i.e., not inherit-only); `Child Folders` + `Child Files` + `All Descendents` are file/dir-inherit. DSM's "This folder only" UI option emits `---n` (no_propagate) on store.

### SAB file permissions

`permissions = 775` in `/config/sabnzbd.ini` (Config → Folders in the UI). Translates to dirs 775, files 664 via SAB's `removexbits` (filesystem.py:670). Group-write on files is required so *arr atomic `link()` imports satisfy `protected_hardlinks` (linker must own file or have group-write). Empty / `664` leaves files at mode 644 and breaks hardlink imports across UIDs in the same group.

## NFS export

As written by DSM to `/etc/exports` after the UI dialog in Phase 0 step 1c:

```
/volume1/media  10.32.25.11(rw,async,no_wdelay,root_squash,insecure_locks,sec=sys,anonuid=1025,anongid=100)
                10.32.25.12(rw,async,no_wdelay,root_squash,insecure_locks,sec=sys,anonuid=1025,anongid=100)
                10.32.25.13(rw,async,no_wdelay,root_squash,insecure_locks,sec=sys,anonuid=1025,anongid=100)
```

`exportfs -v` shows `secure` (privileged source ports required), no `crossmnt`, `no_subtree_check`, `hide`. DSM-side "Map root to guest" materializes as `root_squash` + `anonuid=1025,anongid=100`. Guest (UID 1025) is in DSM's `users` group, NOT `media` — a squashed UID-0 client can traverse + read at POSIX mode 2775 (mode-other) but cannot write.

NFS service config: NFSv4.1 max, sec=sys, no Kerberos. Listening on 2049 (TCP) and 111 (TCP, rpcbind).

## Operational notes captured during bootstrap

- **`synogroup --member` is SET, not append.** Calling it per-user in a loop overwrites the member list each iteration and leaves only the last user in the group. Always pass all members in one call.
- **`getent` is not present on DSM** (busybox ash, not glibc). Use `grep '^<name>:' /etc/passwd` / `/etc/group` for capture.
- **`synouser --add` signature on DSM 7.3.2**: `synouser --add <username> <password> "<full name>" <expired{0|1}> <mail> <privilege>`.
- **Freshly-created DSM share has `@eaDir` and `d---------+` POSIX mode on the share root.** Do not `chown` `@eaDir` (DSM tooling assumes its ownership). For an NFS-only share, the share root needs a real POSIX mode so NFS clients can traverse — chmod scoped to share root + our created subtree, leaving DSM internals alone.
