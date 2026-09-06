# Vonk Forge evaluation

Experimental deployment on 2026-09-05, authorized to replace the active Spark workload temporarily. The controller is at **https://vonk.home.kelch.io**, with native Vonk administrator login. The username is `admin`; the generated password is in the controller bundle's `secrets/admin-password` (also retained locally under the ignored `.private/vonk-evaluation/vonk-forge/` directory). No Tailscale OAuth client is needed.

## Controller

VM 201 `vonk-forge` runs on `pve-sbx-1`, cloned from template 9000. It has 4 vCPU, 12 GiB RAM, a 256 GiB thin disk, and `10.32.21.101/24` on VLAN 21, with gateway/DNS `10.32.21.1`. The VM is excluded from `daily-backups` and has automatic PVE startup disabled. It is disposable; existing VM 200 `hermes-1` is independent.

`vendor-data.yaml` installs Docker and prepares `/srv/vonk`. Its deployed PVE snippet is `library-pve:snippets/vonk-controller.yaml`. The generated controller bundle is `/srv/vonk/vonk-forge` on the VM. Manage it with:

```sh
cd /srv/vonk/vonk-forge
sudo docker compose -f docker-compose.yaml -f compose.local.yaml -f compose.evaluation.yaml ps
sudo docker compose -f docker-compose.yaml -f compose.local.yaml -f compose.evaluation.yaml up -d
```

The second evaluation pass applies the signed, published-compatible controller patch `4d9e967fd67eaa7a73584d4fa2c46f384e26ea64` through `compose.evaluation.yaml`. Include that override in controller Compose commands to retain the evaluated API and worker images. The generated signed Compose file remains intact. See [evaluation results](EVALUATION.md) for measurements and limitations and [custom recipes](recipes/README.md) for current X research, exact model/runtime pins and qualification status.

`compose.local.yaml` profiles out both Tailscale services and adds a Caddy HTTPS front end on the VM's LAN address, proxying to Vonk's native browser edge. The signed generated Compose file is retained intact. `Caddyfile.controller.local` copies the generated native Caddy config and serves its internal recipe-library requests from a read-only cache of the compatible snapshot below. Content comes from the real upstream repository at immutable Git object IDs and passes Vonk's normal digest checks. The adjacent MIT license covers that copied upstream configuration. After changing a Compose config-file source, recreate Caddy with `docker compose -f docker-compose.yaml -f compose.local.yaml -f compose.evaluation.yaml up -d --no-deps --force-recreate caddy`; an ordinary `up` may retain the previous mount. Wait for all active agent operations to finish before recreating the native edge. Hermes is disabled in the installer bundle. Placeholder Tailscale fields are unused; the generated LiteLLM upstream key is only a placeholder for this local-model evaluation.

Browser HTTPS uses a dedicated certificate from `certificate.yaml`, applied manually to cert-manager in namespace `network`. The cluster's current issuer supplies short-lived certificates: the initially installed certificate expires **2026-09-12 18:02 UTC**. Cert-manager renews its Secret, but copying that renewal to the VM is manual for this experiment. Run `./proxmox/guests/vonk-forge/refresh-certificate` before expiry and after subsequent renewals. No cluster credentials or wildcard private key are stored on the VM.

The existing `k8s-gateway` resolver supplies the four `vonk*.home.kelch.io` A records through its hosts plugin. UniFi already delegates `home.kelch.io` to this resolver; no public A record or UniFi policy change is involved. Spark enrollment also installs Vonk's own managed host mappings. The native enrollment, agent mTLS, and registry boundaries remain on TCP 8443 with installer-generated PKI.

## Exact inputs

| Input | Identity |
|---|---|
| Platform checkout | `~/Development/kelchm/vonk-forge`, origin `kelchm/vonk-forge`, upstream `CarstVaartjes/vonk-forge` |
| Platform main inspected | `0ab88b4fa0d95804bf9b3cce10a1c16a5e624eeb` |
| Published dev source | `3cacd73b7817ae884a90d1749380bb1cc246f680` |
| Evaluated controller patch | `4d9e967fd67eaa7a73584d4fa2c46f384e26ea64` |
| Installer generation | `f08e8b4a0650f3b88b94b215a45149e68fc65be87e70fec35d7c5f133e59d00e` |
| Agent package | `0.1.1~dev.540+gf23e9336f7e9` |
| Compatible recipe snapshot | `5bbb0be4e604768499ccbcf82bbea181575c31d6` |

The published controller still consumes the recipe-v1 runtime contract, while the current upstream recipe library has moved to schema 2. Import the compatible snapshot with the importer and validator from the published platform source. This experiment does not add a compatibility shim to the fork. The Qwen3.6 35B-A3B NVFP4 single-Spark recipe and its four catalog dependencies passed that validator and imported successfully, including the verified source bundle. The pinned repository view was validated against all 84 recipes, 250 catalog entities, and every source bundle. Current upstream-main updates are not a usable source of recipes for this controller version.

The stock automatic repository sync imported three recipes, left the existing Qwen recipe unchanged, and skipped 80 after upstream requests failed. Diagnosis found only one request remaining in GitHub's 60-request anonymous API budget, while full hydration requires hundreds of blob requests. This supports rate exhaustion as the cause; individual failed upstream responses were not retained. The local cache removes that dependency without adding a GitHub credential. Generate a new cache directory from the exact Git objects, then copy it alongside the controller's Compose files as `recipe-library-cache/`:

```sh
python3 proxmox/guests/vonk-forge/generate-recipe-library-cache.py \
  --repository .private/vonk-evaluation/recipes-published \
  --output .private/vonk-evaluation/recipe-library-cache
```

The generator refuses another commit or an existing output directory and verifies source blob identities and byte counts before writing. The cache contains 253 files (94,617,370 bytes); its deterministic payload digest is `5607e5c52e7d1ef912163acb9709008c3e8b8a5770a3fd5b8b6bc9f1311bea83`. The published client validated all 84 recipes and hydrated all 84 source bundles from this cache with zero upstream requests. Caddy serves only the pinned repository's commit, index, and available blob paths on internal port 8083; unknown paths return 404. Keep the generated cache outside Git. The live controller subsequently synchronized all 84 recipes: 80 added, four unchanged, zero skipped, and no problems; the frontend reports the repository as current.

The first stock Qwen build failed with `permission-denied`. Its Dockerfile runs `groupadd`, `useradd`, and `install --owner=10001`, but declares no build capabilities. A bounded reproduction of those commands failed with all capabilities dropped, and succeeded with `CHOWN`, `DAC_OVERRIDE`, and `FOWNER`. The local catalog fork `kelchm-qwen36-nvfp4-single` adds those three capabilities during image construction only; runtime capabilities remain empty. `qwen36-recipe.json` records that custom entry. That change allowed Podman to complete, exposing a second contract omission: the source image lacks the required `ai.vonkforge.runtime-interface=v1` label. The custom recipe adds that label through build options and reserves 40 GB for Vonk's uncompressed image export (the stock 12 GB budget does not accommodate an uncompressed vLLM image of this size). The exact pinned Hugging Face revision totals 23,462,477,857 bytes; the stock recipe retained a total 67 bytes smaller from an earlier README revision. The custom recipe corrects those exact artifact sizes, matching the already-correct model-version entry. Full export/install/inference qualification is recorded separately from these fixes.

The corrected image and model installation succeeded. Initial vLLM startup then exposed another recipe problem: `gpu-memory-utilization=0.40` left **−0.2 GiB of available KV cache** after weights, profiling, and CUDA graph memory were accounted for. The current custom recipe defaults to utilization `0.50` and a 65,536-token context, with an 80 GB startup allowance and 70 GB steady-state allowance plus 8 GB growth. It exposes utilization (`0.45`, `0.50`, `0.55`) and context (4,096–262,144 tokens) as runtime parameters. Creating those parameters required a fresh immutable recipe build; subsequent parameter changes can use a new mapping/install/run with that same build and cached model artifacts. Other parameter combinations still require runtime qualification.

The current revision passed build, image distribution, installation, and startup on Spark 2. vLLM reported 14.47 GiB available for KV cache. On 2026-09-06, a real authenticated request to `https://vonk.home.kelch.io/v1/chat/completions`, using alias `qwen36-eval`, returned the requested `VONK MODEL OK` in 1.5 seconds. Automatic route renewal advanced the publication generation, and a second request after the original lease expired also succeeded. That initial result was a text smoke test. The second pass subsequently measured streaming and concurrent throughput and exercised a 17,593-token retrieval prompt, one synthetic tool round trip, and one synthetic vision input; see the bounded claims in [evaluation results](EVALUATION.md). Use the generated LiteLLM key from the private controller bundle for API clients; the browser administrator password is separate.

The working recipe revision is `f54214a4-0a81-4f5d-892f-6ceb039e5d77`, build `e866f750-1bc5-47e5-8046-440c6468e852`, installation `c7feb8ed-0e6d-4add-b1d6-b8f5f0ebbad0`, and current run `6f12c443-c4fb-4a21-8a68-3092e77ca3fe`. The original run `9e7b43ea-8726-457c-9a4f-9607cdc64474` was stopped through the normal lifecycle during the second pass. The final image digest is `sha256:7e86653e0fc9763b1126110d52b077959e8da3f63ed99fd0f1a8a255f9544618`.

Initial endpoint publication failed despite a healthy model: the original publisher waited only 30 seconds for LiteLLM acknowledgement, while its supervisor allowed 120 seconds for startup. A one-off invocation of the normal `RecipeRouteService.publish_run` recovered the existing run with a 90-second acknowledgement wait and the unchanged 120-second evidence lease. The evaluated controller patch now allows acknowledgement within the supervisor budget, bounded by the existing model evidence, and retries transient activation failures through normal validation. A separate real stop failure exposed a 30-second empty-route maintenance lease; that path now has an independent 120-second lease because it grants no model authority. With these fixes, normal stop succeeded in 54.3 seconds; normal start reached runtime health in 248.3 seconds and automatically published in 298.6 seconds. Public inference then succeeded without a recovery script. Unchanged-config lease renewal does not restart LiteLLM.

Vonk reported the failed vLLM process as a readiness-deadline failure and removed its container. Continuous Docker log capture during a second start revealed the actual KV-cache error. Retain a log follower during initial runtime qualification; the controller's generic failure message does not identify every underlying runtime problem.

The original deployment misclassified successful model installations as `rank-incomplete-bytes`: its completeness projection compared installed artifact bytes with the larger disk reservation for download, staging, and cache. The verified installation reports 23,462,480,292 bytes including metadata against a 74,387,433,571-byte reservation. The evaluated controller patch now uses successful exact-rank installation state, and the actual Fleet UI reports complete installations without false warnings. No receipt values were altered. Removal of the obsolete inactive installation released its 74,387,433,571-byte reservation while removing zero shared model bytes; the serving installation remained healthy.

Admission inventory can also appear stale during a long build or transfer. The agent checks its 60-second inventory timer between operations on the same control loop, while telemetry continues independently. After the operation returns, overdue inventory is refreshed before the next job claim. If a preview races that refresh, wait for a new inventory timestamp and retry the preview; restarting the agent is unnecessary.

Private deployment artifacts, generated credentials, exact controller image digests, API operation receipts, and baseline Docker inspections are retained in `.private/vonk-evaluation/`. Detached checkouts there preserve the published importer and compatible recipe snapshot. Never commit that directory or the generated bundle.

## Transfer observations

The corrected Qwen image completed build, export, and controller verification with a 20,775,103,488-byte uncompressed archive. Upload throughput varied around 20–40 MB/s. A bounded 512 MiB memory-to-memory transfer from Spark 2 to the controller VM reached 256 MB/s (2.05 Gbit/s) while the upload was active. The Spark management link negotiates at 10 Gbit/s; the controller's PVE host uplink negotiates at 2.5 Gbit/s. The 200 Gbit/s Spark fabric does not carry controller transfers.

During upload, the control API used approximately 170% CPU while guest disk wait stayed near zero. The published agent uploads through a default 4 KiB Tokio `ReaderStream`; the API hashes each received chunk and serially awaits a thread-pool file write. This suggests application scheduling overhead. Later controlled network comparisons did not establish a reliable benefit from controller write batching, despite a large body-only microbenchmark gain; those changes were excluded. See [evaluation results](EVALUATION.md) for the measured boundaries and remaining native-transfer investigation. The native mTLS upload uses port 8443 and does not traverse the local browser HTTPS override. No upload optimization has been deployed.

A brief native Caddy restart after the archive download but during Docker import exposed an operation-recovery limitation: the agent heartbeat task exits on a single transport error. Docker import completed successfully on the Spark, but the controller expired its lease and required the supported operation retry. That retry downloads the archive again even when the exact image is already loaded. Keep the native edge uninterrupted throughout build, distribution, install, and run operations; the local HTTPS certificate refresh restarts only the separate browser edge.

## Sparks and rollback

Both agents use their existing management addresses (`10.32.21.31` and `.32`). Their configured primary fabric is `198.19.240.11` ↔ `.12`, with 200 Gbit/s bandwidth. Both physical fabric links and the original no-transit nftables guard remain in place; the default route stays on VLAN 21. No model caches or old images were deleted.

Before installing either agent, the active `glm53-exl3-head` and `glm53-exl3-worker` containers were stopped. Each host retains `/home/kelchm/vonk-evaluation-backup/`, containing Docker inspections, image digests, network state, host configuration, and the original GLM launcher files. The launch checkout was `/home/kelchm/glm53-guide` at `c707598ebcf02fd827d079a7c47e785069425efe`.

To return to the previous workload:

1. Stop active Vonk recipe runs through the controller and confirm their containers have exited.
2. Stop any remaining Vonk build/install operations, then disable the agent, helper socket, and firewall on both Sparks: `sudo systemctl disable --now vonk-forge-agent.service vonk-forge-package-helper.socket vonk-forge-docker-firewall.service`; stop `vonk-forge-package-helper.service` too. Vonk's Docker firewall now limits host endpoint port 8888 to the controller. Verify the `vonk-forge-managed-v1` marker in `iptables -S VONK-FORGE` and `iptables -S VONK-FORGE-HOST`, remove the `DOCKER-USER → VONK-FORGE` and `INPUT → VONK-FORGE-HOST` jumps, then flush/delete only those two chains. Disabling the firewall service also removes its `docker.service.wants` link. Preserve `dgx-fabric-isolation.service` and the `inet dgx_fabric_guard` table. Compare against `host-config.tgz` and `nftables.txt` in the baseline backup; do not flush the entire firewall.
3. Start the retained worker container on Spark 2, then the head on Spark 1: `docker start glm53-exl3-worker` and `docker start glm53-exl3-head`. Validate the original endpoint before resuming clients. If the retained containers cannot start, compare their inspected configuration and firewall state to the backup, then relaunch from the retained `glm53-guide` checkout and launcher files.
4. Stop VM 201. To fully tear down the experiment, revert the entire `extraZonePlugins` addition in `k8s-gateway` to restore chart defaults, delete the dedicated Certificate and Secret, and clear VM 201 from the backup exclusion in the same teardown window immediately before deleting the stopped VM. Preserve the baseline backups until GLM has passed a real inference request.

The separate `/opt/spark-models`, Hugging Face caches, and prior inference stack were retained. This deployment does not prove dual-node model execution merely by enrolling both Sparks.
