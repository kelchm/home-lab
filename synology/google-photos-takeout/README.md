# Google Photos collection from Takeout

One [rclone](https://rclone.org) container copies Google Takeout archives from Google Drive to Athena every day and extracts each export into its own directory. Google Takeout is the only official way to get the originals of a whole Google Photos library: since 2025-03-31 the Photos API reads only media the calling app uploaded. Background and the routes that were rejected are in [home-lab#768](https://github.com/kelchm/home-lab/issues/768); the work is tracked in [home-lab#781](https://github.com/kelchm/home-lab/issues/781).

State as of 2026-10-06: the Takeout schedule is armed. The collector has not run on Athena. Its script was run locally against synthetic archives, including a damaged one and a stale state; it has not yet read from Drive or handled a real export.

## What is where

| Thing | Value |
|---|---|
| Takeout schedule | Google Photos only, Add to Drive, every month for 1 year, only new and updated items after the first export, `.zip`, 10 GB parts; armed 2026-10-06 |
| Source | The `Takeout` folder in the owner's Drive, read with the `drive.readonly` scope |
| Exports | `/volume1/personal-data/google-photos/<export time>/Takeout/Google Photos/...`, one directory per export |
| Working state | `/volume1/docker/google-photos-takeout/archives` (archives in flight) and `state/` (`extracted.txt`, `last-arrival`) |
| Schedule | 04:00 UTC daily: start ping, copy new archives, extract, then a ping with the run status |
| Dead-man check | healthchecks.io `athena-google-photos-takeout`, cron `0 4 * * *` UTC, 3 hours grace, declared in [`healthchecks/checks.json`](../../healthchecks/checks.json) |

Each run copies every `takeout-*.zip` not yet listed in `state/extracted.txt`, checked against Drive's checksum. An archive is then extracted, which verifies each file's CRC, recorded in `extracted.txt` and deleted locally. The collector never deletes or changes anything in Drive, and never deletes an export.

Exports are kept apart on purpose. Google reuses file names between exports, so merging two exports into one tree can overwrite a different photo that has the same name. The first export of a schedule is the whole library and later ones hold only new and updated items; within an export, a photo in an album appears both in its year folder and in the album folder. The off-site job deduplicates identical files, so neither repetition costs storage there.

A run fails, and the check goes down about three hours later, when the copy or an extraction fails or when no archive has arrived for 40 days. The second case covers a schedule that ended, a revoked token that still lists an empty folder, and Google not producing an export.

## Credentials

| Secret | On Athena | In 1Password (Private) |
|---|---|---|
| Google OAuth client ID and secret | Yes, in `secrets.sops.env` | `athena-google-photos-takeout-oauth` |
| Drive refresh token | Yes, in `secrets.sops.env` | No; authorise again to replace it |
| healthchecks.io ping URL | Yes, in `secrets.sops.env` | No; read it back with the project API key |

The token can read the owner's whole Drive and nothing else: it cannot read Google Photos or Gmail, and cannot write or delete. `secrets.sops.env` is encrypted to the NAS-scoped age recipient in [`.sops.yaml`](../../.sops.yaml). The container writes the rclone configuration to a tmpfs at start, so refreshed tokens are never stored on disk.

Create the OAuth client once, in the [Google Cloud console](https://console.cloud.google.com/):

1. Create a project and enable the **Google Drive API**.
2. Under **Google Auth Platform → Branding**, set an app name and support email.
3. Under **Audience**, choose **External**, then **Publish app**. An app left in "Testing" loses its grant after seven days. Google does not require verification for a personal app with fewer than 100 users; the consent screen shows an "unverified app" warning.
4. Under **Clients**, create a client of type **Desktop app** and note its ID and secret.

Authorise it on a workstation, which opens a browser, then print the token:

```sh
rclone config create gdrive drive scope=drive.readonly client_id=<id> client_secret=<secret>
rclone config show gdrive
rclone lsf gdrive:Takeout
```

Put the values into the secrets file with the NAS key. `GDRIVE_TOKEN` is the whole JSON object from the `token =` line, on one line.

```sh
SOPS_AGE_KEY="$(op read 'op://Private/athena-doco-cd-sops-age-key/password')" sops synology/google-photos-takeout/secrets.sops.env
```

Afterwards remove the workstation copy with `rclone config delete gdrive`.

## First deployment

1. Create the shared folder `personal-data` in DSM, with no access for users or guests, then the directories:

   ```sh
   ssh kelchm@10.32.20.5 'sudo mkdir -p /volume1/personal-data/google-photos /volume1/docker/google-photos-takeout && sudo chmod 700 /volume1/personal-data/google-photos /volume1/docker/google-photos-takeout'
   ```

2. Run `task healthchecks:apply` to create the check, read its ping URL, and set `HC_PING_URL` together with the Google values in `secrets.sops.env`.
3. For the first run only, raise the check's `grace` in `healthchecks/checks.json` to cover pulling the whole library, apply it, and put it back afterwards.
4. Merge. doco-cd deploys the project, and the container waits for 04:00 UTC.

## Check on it

```sh
ssh kelchm@10.32.20.5 'sudo /usr/local/bin/docker logs --since 26h google-photos-takeout-collector-1'
ssh kelchm@10.32.20.5 'sudo ls -la /volume1/personal-data/google-photos; sudo cat /volume1/docker/google-photos-takeout/state/extracted.txt'
```

## Upkeep

- Once a year the Takeout schedule ends. Create it again at [takeout.google.com](https://takeout.google.com) with the settings above; it starts with a new full export. If this is missed, the check goes down 40 days after the last archive.
- Delete archives from the `Takeout` folder in Drive once they appear in `extracted.txt`. They count against the Google storage quota, and the collector's read-only token cannot remove them.
- Old exports on Athena are not pruned. Removing one by hand also removes, at the next off-site retention cycle, the only copy of any photo since deleted from Google Photos.

## Using the files

Originals keep their own EXIF. Captions, albums, dates and locations edited in Google Photos are in the `.json` file beside each photo and in each album folder's `metadata.json`, not in the image. Edited versions are separate files with an `-edited` suffix in the year folder. [immich-go](https://github.com/simulot/immich-go) reads these directories as they are.

## Limits

- A photo added and deleted between two exports is never collected. With monthly exports the exposure for new photos is up to about a month plus the time Google takes to build the archive.
- What counts as "updated" in an incremental export is not documented by Google. It will be recorded here from the first increment.
- Losing `state/extracted.txt` makes the next run copy and extract again every archive still in Drive. That repeats work and changes nothing in the exports.
