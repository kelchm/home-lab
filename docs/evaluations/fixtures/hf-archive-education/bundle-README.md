# hf-archive educational evidence bundle, October 4, 2026

> **Reference copy, noted October 4, 2026.** This is a copy of the README inside `hf-archive-education-20261004.tar.gz`, a bundle delivered outside this repository; everything below the note is that README unchanged. The paths it lists are paths inside the tarball, not files in this checkout. The repository holds only this copy and the compact recorded results beside it, not the tarball or its harness scripts, and no public download location is provided. The operator's copy is on the workstation at `/Users/kelchm/Downloads/hf-archive-education-20261004.tar.gz` (188,797 bytes, SHA-256 `a654feec5f490b41dfb622c2171e4b892d0318e2855dc77d334fcc16cbdec157`). The extraction and re-run commands below apply only to someone holding a copy of that tarball with a matching checksum. The recorded results can be read without it; probes rebuilt from the write-up would be new tests, not a re-run of the recorded harness.

Scripts, dependency pins and recorded results from one day of workstation-only testing of the `hf-archive` prototype, kept so the lessons can be checked or repeated without the original machine. The write-up is `evidence/EDUCATIONAL-QUALIFICATION.md`, under "Educational follow-up, October 4, 2026". This bundle supports that write-up; it is not a deployment artifact and makes no case for adopting the prototype.

## What is inside

| Path | Contents |
| --- | --- |
| `evidence/EDUCATIONAL-QUALIFICATION.md` | The qualification record including the October 4 follow-up. |
| `tools/hf-archive/` | The package source exactly as at commit `9c119dc60b4b93725d436e066450ef4af6a2e7f3`, including its own older `QUALIFICATION.md`, which predates the follow-up. |
| `sdk-probe/` | Loopback fixture and probe for unmodified `huggingface_hub` 1.8.0 and 2.1.1, pins for both, and the recorded results of 42 cases (`summary.json`, `evidence.json`, `run.log`). |
| `cold-coordinator/` | The experimental cold-request gateway, worker, client and harness, with recorded results of 10 scenarios and 120 checks under `results/`. This was never part of the package. |
| `nas-workstation/` | Scripts and result files from the re-run against the NAS, including `cleanup.json` and `nas-final-verify.json`. |
| `patches/`, `provenance/originals/` | What was changed to make the scripts portable, and the scripts as originally run. |
| `manifest.json` | Size and SHA-256 of every file, exclusions, and the reproduction commands. |

Not included: model payloads, client caches, archive blobs, virtual environments, the per-case SDK probe directories, credentials or tokens.

## Recorded evidence and adapted scripts

The result files are the original recorded output and were not regenerated. They still mention the original absolute paths.

The runnable scripts are convenience copies with mechanical changes only: paths resolved relative to the extracted bundle, and caches, temporary files and Hugging Face token paths confined to directories inside it. Request timeouts and retry settings are unchanged. Each adapted script has its diff in `patches/`, its original in `provenance/originals/`, and both hashes in `manifest.json`.

The recorded results came from the originals. The 42 SDK cases and 10 coordinator scenarios were not re-recorded with the adapted copies, so a re-run is a new run to compare against the recorded results, not a replay of them.

## Re-running the loopback harnesses

Needs `uv`, network access to install the pinned Python 3.12.13 and dependencies, and permission to open loopback sockets and start subprocesses. No credentials are needed, and once dependencies are installed the tests talk only to their own loopback fixtures. Run from a fresh extraction: the commands overwrite the result files in place, so keep the tarball.

```sh
tar -xzf hf-archive-education-20261004.tar.gz
cd hf-archive-education-20261004

# SDK probe: 42 cases, several minutes. Pass the uv binary itself, not a version-manager shim.
./sdk-probe/setup-and-run.sh /absolute/path/to/uv

# Cold-request experiment: 10 scenarios; the recorded run took 37 seconds.
(cd tools/hf-archive && uv sync --locked --dev --python 3.12.13)
tools/hf-archive/.venv/bin/python cold-coordinator/harness.py
```

The SDK probe refuses to start if `sdk-probe/cases/` already exists; that directory is not shipped, so a fresh extraction is ready to run. `manifest.json` records a stricter form of the coordinator commands that also isolates the environment.

## The NAS scripts are a record, not a procedure

The files under `nas-workstation/` are specific to the original host: they name its SSH host, user, container name and ports. The container, network, image and task directory they used were removed afterwards (`cleanup.json`), and nothing in this bundle recreates them or touches a NAS on its own.

Repeating that run means deliberately setting up an isolated container and network on a NAS, pulling the pinned public `prajjwal1/bert-tiny` files again from the live Hub and checking them against the recorded hashes, then adjusting the scripts to match. Do not point them at a production service. No Spark host is needed for anything here.

`nas_client.py` in resume mode asserts that a seeded partial file is consumed. That assertion fails on SDK 2.1.1 by design of that version; `sdk-2.1.1-legacy-seed.json` records the result and its explanation.
