#!/bin/sh
# Apply NFSv4 ACLs to /volume1/media/{tv,movies,music,.downloads,books,audiobooks}
# per the design in docs/plans/20260513-arr-hardlink-rework.md.
#
# Run on the Synology NAS as root (sudo -i). Idempotent — safe to re-run.
# Source of truth for the per-folder ACL state; the DSM Properties UI is for
# inspection, not modification.

set -eu

# ---- DSM-version-dependent constants ----------------------------------
# Verified against DSM 7.3.2 (build 86009) on 2026-05-13. Re-run the probes
# in docs/plans/20260513-arr-hardlink-rework.md if you upgrade DSM and the
# script starts failing.
#
# Permission slot is fixed-width 13 chars in positional order
#   r-w-x-p-d-D-a-A-R-W-c-C-o
# where `-` means the bit is unset. Letter meanings per `synoacltool -h`:
#   r  read_data            (Read → List folders/Read data)
#   w  write_data           (Write → Create files/Write data)
#   x  execute              (Read → Traverse folders/Execute files)
#   p  append_data          (Write → Create folders/Append data)
#   d  delete               (Write → Delete)
#   D  delete_child         (Write → Delete subfolders and files)
#   a  read_attributes      (Read → Read attributes)
#   A  write_attributes     (Write → Write attributes)
#   R  read_xattr           (Read → Read extended attributes)
#   W  write_xattr          (Write → Write extended attributes)
#   c  read_acl             (Read → Read permissions)
#   C  write_acl            (Administration → Change permissions)
#   o  take_ownership       (Administration → Take ownership)

# Manager — full lifecycle minus delete-of-self. `D` delete-child allows
# removing the folder's contents recursively; `d` absent prevents rmdir of
# the category folder itself. `C`/`o` absent blocks ACL/ownership changes.
PERMS_MANAGER="rwxp-DaARWc--"

# Reader — read + traverse + read attrs/xattr/acl.
PERMS_READER="r-x---a-R-c--"

# Used for Deny ACEs only — every NFSv4 bit, so the Deny scope is total.
PERMS_DENY_ALL="rwxpdDaARWcCo"

# Inherit-flag slot is fixed-width 4 chars: f-d-i-n
#   f  file_inherit         (Apply To: Child Files)
#   d  directory_inherit    (Apply To: Child Folders)
#   i  inherit_only         (ACE stored here but does NOT apply here)
#   n  no_propagate         (inherit to direct children only)

# This folder + all descendants. Used for everything that should propagate.
INHERIT_ALL="fd--"

# This folder only, no inheritance. DSM encodes "This folder" alone as
# ---n; matching that encoding keeps re-runs idempotent.
INHERIT_LOCAL="---n"

# ---- target folders (must exist) --------------------------------------
PATHS="
/volume1/media
/volume1/media/tv
/volume1/media/movies
/volume1/media/music
/volume1/media/.downloads
/volume1/media/books
/volume1/media/audiobooks
"

# ---- ACE list ---------------------------------------------------------
# Tab-separated columns: path \t type \t principal \t action \t perms \t inherit
# `type`   = user | group | owner | everyone | authenticated_user | system
# `action` = allow | deny
#
# `media` group membership: qbittorrent, sabnzbd, sonarr, radarr, lidarr,
# readarr, bazarr, unpackerr. Jellyfin is NOT a member (it's a library
# consumer, not a producer) — its access goes via the user:jellyfin ACE
# on the share root, with an explicit Deny on /.downloads.
#
# Synology's synoacltool canonicalizes Deny-first regardless of insertion
# order, so the user:jellyfin Deny on /.downloads correctly precedes the
# inherited Allow ACEs at evaluation time.

ACES="
# === share root: admin override (inherits), media traverse-only, jellyfin reader (inherits) ===
/volume1/media	group	administrators	allow	$PERMS_MANAGER	$INHERIT_ALL
/volume1/media	group	media	allow	$PERMS_READER	$INHERIT_LOCAL
/volume1/media	user	jellyfin	allow	$PERMS_READER	$INHERIT_ALL

# === /tv: sonarr + bazarr manage ===
/volume1/media/tv	user	sonarr	allow	$PERMS_MANAGER	$INHERIT_ALL
/volume1/media/tv	user	bazarr	allow	$PERMS_MANAGER	$INHERIT_ALL

# === /movies: radarr + bazarr manage ===
/volume1/media/movies	user	radarr	allow	$PERMS_MANAGER	$INHERIT_ALL
/volume1/media/movies	user	bazarr	allow	$PERMS_MANAGER	$INHERIT_ALL

# === /music: lidarr manages ===
/volume1/media/music	user	lidarr	allow	$PERMS_MANAGER	$INHERIT_ALL

# === /books, /audiobooks: no explicit writer ACEs (Readarr replacement pending) ===
# Access comes from inherited administrators (Mgr) and user:jellyfin (Rdr).

# === /.downloads: media group writes; jellyfin explicitly denied (overrides inherited Reader) ===
/volume1/media/.downloads	group	media	allow	$PERMS_MANAGER	$INHERIT_ALL
/volume1/media/.downloads	user	jellyfin	deny	$PERMS_DENY_ALL	$INHERIT_ALL
"

# ---- application logic ------------------------------------------------

strip_comments_and_blanks() {
    printf '%s\n' "$1" | grep -v '^[[:space:]]*$' | grep -v '^[[:space:]]*#'
}

main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: must run as root (sudo -i)" >&2
        exit 1
    fi

    paths=$(strip_comments_and_blanks "$PATHS")
    for p in $paths; do
        if [ ! -d "$p" ]; then
            echo "ERROR: target path missing: $p" >&2
            exit 1
        fi
    done

    backup="/var/log/media-acls-backup-$(date +%Y%m%d-%H%M%S).txt"
    echo "=== Backing up current ACL state to $backup ==="
    for p in $paths; do
        {
            echo "--- $p ---"
            ls -ld "$p" 2>&1 || true
            synoacltool -get "$p" 2>&1 || true
        } >> "$backup"
    done

    # Two states where `synoacltool -del PATH` errors:
    #   - "It's Linux mode" — no Synology ACL attached at all (bootstrap
    #     state after `chmod 2775`).
    #   - "ACL version" with no level:0 entries — only inherited ACEs
    #     exist. -del errors with "Index out of range". Re-query state per
    #     iteration since the prior path's -del may have changed this
    #     path's inheritance source.
    echo "=== Resetting folder ACLs ==="
    for p in $paths; do
        state=$(synoacltool -get "$p" 2>&1 || true)
        case "$state" in
            *"It's Linux mode"*)
                printf 'skip (Linux mode): %s\n' "$p"
                ;;
            *"ACL version:"*)
                if printf '%s\n' "$state" | grep -q '(level:0)'; then
                    printf 'reset (has local ACEs): %s\n' "$p"
                    synoacltool -del "$p"
                else
                    printf 'skip (inherit-only, no local ACEs): %s\n' "$p"
                fi
                ;;
            *)
                echo "ERROR: unexpected synoacltool -get output for $p:" >&2
                printf '%s\n' "$state" >&2
                exit 1
                ;;
        esac
    done

    echo "=== Applying target ACEs ==="
    strip_comments_and_blanks "$ACES" | \
    while IFS="$(printf '\t')" read -r path type principal action perms inherit; do
        ace="${type}:${principal}:${action}:${perms}:${inherit}"
        printf '  + %s on %s\n' "$ace" "$path"
        synoacltool -add "$path" "$ace"
    done

    # `synoacltool -del` clears the is_inherit archive bit along with
    # the ACEs, and `-add` doesn't restore it — the result is a
    # category folder in ACL mode with only its own level:0 ACEs and no
    # inherited entries from /volume1/media. Explicitly re-enable
    # inheritance on each managed path. Safe to call on the share root
    # (no-op when there's no parent ACL to inherit from).
    echo "=== Re-enabling inheritance on managed folders ==="
    for p in $paths; do
        printf 'set-archive is_inherit: %s\n' "$p"
        synoacltool -set-archive "$p" is_inherit 2>&1 || true
    done

    # NOTE: we don't migrate Linux-mode descendants here. `synoacltool
    # -set-archive PATH is_inherit` only works on entries that already
    # have a Synology ACL attached — it cannot promote a Linux-mode
    # entry into ACL-mode (errors with "Not allowed to do it"). The DSM
    # UI's "Apply to this folder, sub-folders and files" checkbox does
    # the transition via a code path synoacltool doesn't expose. So
    # existing Linux-mode descendants stay Linux-mode.
    #
    # This is fine because parent-level ACLs gate access — Jellyfin is
    # denied at `/.downloads` itself, so the mode of descendants under
    # `/.downloads/usenet/...` doesn't matter. New content created
    # directly under an ACL-mode parent (e.g. a new show folder under
    # `/tv`) inherits the ACL from the start.
    #
    # If a full ACL migration of existing content is ever needed, use
    # the DSM UI: right-click the folder → Properties → Permission →
    # check "Apply to this folder, sub-folders and files" → OK.

    echo "=== Final ACL state ==="
    for p in $paths; do
        echo "--- $p ---"
        synoacltool -get "$p"
    done

    echo
    echo "Done. Backup: $backup"
}

main "$@"
