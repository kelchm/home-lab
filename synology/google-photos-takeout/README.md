# Google Photos collection from Takeout (inactive)

Prepared code only, as of 2026-10-28. This project is absent from `synology/.doco-cd.yml`, the deployed-project table and `healthchecks/checks.json`. Merging it does not deploy collection or finish [#781](https://github.com/kelchm/home-lab/issues/781). Compose also requires the `inactive` profile and refuses to start collection unless `COLLECTOR_ENABLED=true` and non-placeholder credentials are supplied. The encrypted file still contains placeholders. No production collector deployment is recorded.

One pinned, stock [rclone](https://rclone.org) container is prepared to copy scheduled Takeout ZIPs from Drive, classify their members and extract Photos parts into separate export directories. The original implementation and research were by Claude Opus 5.5; the inactive split, classifier, state and fixture coverage revise that work. Background is in [#768](https://github.com/kelchm/home-lab/issues/768).

## What is where

| Thing | Value |
|---|---|
| Takeout schedule | Google Photos only, Add to Drive, monthly for one year, only new and updated items after the first export, ZIP, 10 GB parts; armed 2026-10-06 |
| Source | `gdrive:Takeout`, with the owner's published OAuth client and `drive.readonly` grant; Timeline also uses this folder |
| Photos output | `/volume1/homes/kelchm/cloud-backups/google-photos/<UTC export timestamp>/Takeout/Google Photos/...` |
| Scratch | `/volume1/docker/google-photos-takeout/archives/`, including failed and deliberately ignored ZIPs |
| State | `/volume1/docker/google-photos-takeout/state/version` and `decisions/` |
| Intended schedule | Daily at 04:00 UTC, with start/result pings after activation |
| Intended dead-man check | `athena-google-photos-takeout`, cron `0 4 * * *` UTC, initial grace sized for the first full run; not declared or active here |

The first full export finished on October 6: 24 data parts, approximately 254.3 GB of ZIPs. Small companion archives and a separate Timeline export also exist. Their real Photos layout, index and timestamp meaning remain unconfirmed. The next increment is expected around November 6. The four planned edits (album membership, caption, crop, deletion) must precede that export; owner confirmation of whether/when they were made is pending. Collection may follow while those archives remain in Drive.

## Acceptance and freshness

The source inventory uses rclone's CSV output and requires unquoted observed-style `takeout-YYYYMMDDTHHMMSSZ-001.zip` or `-1-001.zip` names, an available MD5 and a size. Malformed, ambiguous or duplicate names fail the run. Filename shape selects candidates and supplies the provisional export timestamp; it never proves product identity. Dates must round-trip through the calendar and cannot be in the future.

Each uncommitted identity is copied with `--checksum`, then the local MD5 and size must match the source inventory before inspection. Files are processed one at a time, rather than copying the entire batch before extraction. Scratch needs room for the largest part plus retained ignored/failed ZIPs and any in-flight transfer; expanded output needs separate space. The collector makes no remote changes and never prunes retained exports.

The classifier reads the complete raw member listing before touching Photos output. It requires actual file members beneath the exact `Takeout/Google Photos/` root. A directory alone, a lookalike root or a mention of Photos in a filename is insufficient. A Timeline-only payload is recorded as ignored; mixed products, unknown layouts, unsafe/ambiguous paths and metadata-only parts are quarantined in scratch and fail the run. Until the real `Takeout/archive_browser.html` schema is confirmed, **all index-bearing parts are held outside output and freshness**, including an index accompanying payload. No empty-increment success is assumed.

Pinned rclone has no JSON archive-list mode. Its [raw list command](https://github.com/rclone/rclone/blob/v1.75.1/cmd/archive/list/list.go) prints unescaped names; the [archive backend](https://github.com/rclone/rclone/blob/v1.75.1/backend/archive/zip/zip.go) can hide unsafe paths. The collector therefore cross-checks the raw sized and plain listings: removing a size prefix from every sized line must reproduce the plain listing byte for byte. An embedded newline cannot satisfy both formats. Control characters, backslashes, traversal/cleaned paths, duplicates, file/directory aliases and nonempty directory bodies are rejected. Unicode, ordinary spaces and trailing spaces are preserved. This uses only tools already in the pinned image.

Accepted data ZIPs are extracted **without filters** using [rclone archive extract](https://github.com/rclone/rclone/blob/v1.75.1/cmd/archive/extract/extract.go), reading every file body and checking its CRC. Failed extraction can leave partial files within that export's directory; it creates no acceptance and is retried. Separate parts of one export share that directory; different exports never share output. A Photos part proves only its own contents, with no claim about the whole export's products or multipart completeness.

Freshness is the maximum UTC filename timestamp among successfully accepted Photos identities, **not processing time or a touched file**. Exactly 40 days is stale. Replayed or late older parts cannot advance or regress that maximum, even after source ZIPs are removed. Any inventory, copy, classification, extraction or state error fails that run despite earlier fresh Photos data. A Timeline arrival cannot refresh Photos health.

## State and retry

`photos-v1` versions both the ledger and classifier. Each decision filename is `<archive name>.<source MD5>.<size>`; its contents record version, accepted/ignored/quarantined decision, export epoch and reason. Changed same-name content gets a new identity and must be classified again. Earlier accepted identities remain historical freshness evidence. Accepted state is committed by atomic rename **after** verified copy and successful full extraction, with write and rename errors checked explicitly. There is no separately committed freshness timestamp.

Accepted ZIPs are removed from scratch after commit. If interrupted between commit and removal, the next run skips extraction of that identity and removes the leftover cache. Ignored Timeline ZIPs stay in scratch but are skipped for the same source fingerprint. Quarantined ZIPs stay available for diagnosis and are retried while present in the source. A repaired same-name source does not inherit the failed decision. No output or source files are automatically deleted to resolve a failure.

A missing version marker with nonempty state, scratch or output is refused. Old `extracted.txt`/`last-arrival` files, an unsupported version or invalid ledger also fail visibly. **Preserve those files first.** Use new empty work and output paths, retain the old trees for comparison, and explicitly revalidate the retained source ZIPs before considering a replacement authoritative. Do not seed acceptance from filenames or import local arrival times. There is no automatic legacy migration: no production installation has been recorded.

## Verification

The focused harness executes the actual inline Compose command in the exact pinned image, with generated ZIPs, local read-only source, `--network none`, UID/GID 1026:100, dropped capabilities, read-only root and tmpfs token/config paths. Only the clock and selected I/O failures are injected. CI runs it in the Synology job. Local use with the authorized test context:

```sh
python3 scripts/ci/test-google-photos-takeout.py --context colima-tf-build
```

Fixtures cover Photos parts, Unicode/trailing spaces, Timeline/mixed/unknown/index-only/lookalike layouts, ambiguous and unsafe members/source names, CRC failure/retry, copy and state-write/rename failures, restart after commit, changed same-name content, invalid/lost/legacy state, old exports first processed today, the exact 40-day boundary, old-after-new, source removal, and malformed/future timestamps. Missing, encrypted and placeholder credentials cannot start the normal collector.

## Before activation

Activation is a separate reviewed change under #781, with explicit deployment authority. It must confirm the actual Photos root, wrapper/index product information, filename timestamp meaning and empty-increment semantics. Refine the fail-closed classifier from that evidence without treating one part as proof of the whole export. Reading a ZIP index does not verify its file CRCs. Drive authorization, abuse acknowledgement with the read-only grant, range behavior and large-part transfers remain untested.

1. Create the owner's Google OAuth client and authorize the `drive.readonly` scope. Publish the app rather than leaving it in Testing. In the [Google Cloud console](https://console.cloud.google.com/), enable Drive API, configure the consent screen for the personal app and create a Desktop client. On a workstation:

   ```sh
   rclone config create gdrive drive scope=drive.readonly client_id=<id> client_secret=<secret>
   rclone config show gdrive
   ```

   `GDRIVE_TOKEN` is the complete one-line JSON token, including a nonempty refresh token. Store client ID/secret and token in `secrets.sops.env`, encrypted to the NAS recipient in [`.sops.yaml`](../../.sops.yaml), using the existing secret-management procedure. Remove the workstation remote afterwards with `rclone config delete gdrive`. No real secret was accessed for this change.

2. Recheck free space; the 28 TB observation is from October 11. Create the output and work directories owned by `kelchm:users` (1026:100), with private permissions. Exports live on the `homes` share with four-hourly DSM snapshots; scratch is outside those snapshots. Preserve any unexpected existing files and follow the revalidation procedure above.
3. Add the doco-cd registration, deployed-project row and healthcheck declaration; create the real check and store its `HC_PING_URL`. Size first-run grace for the full transfer/extraction, then restore normal grace. Remove the inactive profile and explicitly enable collection only in that activation change.
4. Keep the runtime script inline. Long-running doco-cd services cannot bind-mount repository files: per-revision checkouts disappear. Retain the published image digest, UID/GID, read-only root, dropped capabilities and tmpfs-only token configuration. The Drive token can read the owner's Drive but cannot modify or delete it.
5. Observe full-export extraction, every archive's integrity, then an increment and stale-export failure on Athena. Record what the increment contains for the four test edits. These operations have not happened in this change and remain #781 acceptance work.

## Upkeep and restore after activation

- Re-arm Takeout annually with the chosen Photos-only schedule; the schedule ends after a year and a new schedule starts with a full export. The yearly behavior still needs owner observation. Missing it eventually fails export-age health.
- Inspect the container logs and `state/decisions/` before manually deleting any source archive from Drive. Confirm its exact name, MD5 and size are accepted, rather than relying on its name alone. Retain unresolved/index parts for diagnosis. The read-only collector never performs this cleanup.
- Keep exports separate. Google reuses filenames across exports; older exports may contain the only retained copy of a photo later deleted from Google. No automatic retained-export pruning is provided.
- Originals retain embedded metadata; Google Photos edits and album information may also be in JSON sidecars and separate edited files. Restore the selected export tree together with its sidecars; tools such as [immich-go](https://github.com/simulot/immich-go) can consume Takeout trees. Actual increment contents remain to be documented from the first full-plus-increment acceptance.
- A photo created and deleted between monthly exports may never be collected. The source Drive archives and NAS snapshots are not a completed off-site backup: file the separately scoped off-site inclusion follow-on before closing #781.
