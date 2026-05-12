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
| readarr     | 1041 | books/audiobooks RW + .downloads RW|
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
