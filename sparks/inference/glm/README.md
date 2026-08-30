# GLM-5.3-Flash EXL3 on two DGX Sparks

Runs [MiaAI-Lab's two-Spark guide](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks) at commit `79f10b91f84779b2b1ff2c9327b1a5847cd97f70`. The guide owns the native host-network launch; this directory carries only the site overrides and immutable artifact pins consumed by the Ansible profile.

This is the EXL3 + DFlash2 path validated on spark-1/spark-2, not Tony's NVFP4 GLM path. Do not add `--moe-backend marlin`, NVFP4 KV, or a second launcher. The target KV path is `fp8_ds_mla`; DFlash2 uses its default BF16 draft KV and `FLASH_ATTN`.

## Stage the guide and artifacts

Clone and pin the guide on both hosts:

```sh
git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks.git ~/glm53-guide
git -C ~/glm53-guide checkout --detach 79f10b91f84779b2b1ff2c9327b1a5847cd97f70
```

On spark-1, start from the pinned `.env.example` and apply every committed override. The loop replaces uncommented defaults, uncomments optional defaults, and appends keys absent from the upstream example:

```sh
cd ~/glm53-guide
cp .env.example .env
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  key=${line%%=*}
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${line}|" .env
  elif grep -q "^# ${key}=" .env; then
    sed -i "s|^# ${key}=.*|${line}|" .env
  else
    printf '%s\n' "$line" >> .env
  fi
done < /path/to/home-lab/sparks/inference/glm/env.overrides
```

The model revision is pinned by `.env`. Install the same lightweight Hub client used during validation, then stage DFlash2 at its pinned revision before the guide's download helper runs; the helper otherwise follows the Hub default branch:

```sh
python3 -m venv ~/.hf-cli/venv
~/.hf-cli/venv/bin/pip install 'huggingface_hub==0.35.3'
HF_HOME=/home/kelchm/.cache/huggingface ~/.hf-cli/venv/bin/hf download incoai/GLM-5.3-Flash-DFlash2 --revision dc77ff1c99eeb2df044ee3d4f0094eb033fee410
mkdir -p ~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2/refs
printf %s dc77ff1c99eeb2df044ee3d4f0094eb033fee410 > ~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2/refs/main
./download.sh
```

Pre-pull the digest-pinned image on both hosts, then copy the two pinned cache trees to spark-2 over fabric-a. The preflight requires the same image ID, exact refs, all 120 target shards, and the DFlash2 safetensors file on both ranks; a switch never pulls, downloads, or syncs implicitly.

```sh
docker pull ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks@sha256:9bb1557a4234fce63d59599e44d10747eabd742beb337eebf9e7070be8a0fd58
ssh 198.19.240.12 docker pull ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks@sha256:9bb1557a4234fce63d59599e44d10747eabd742beb337eebf9e7070be8a0fd58
rsync -a --partial --info=progress2 ~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-TR3-4bpw/ 198.19.240.12:.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-TR3-4bpw/
rsync -a --partial --info=progress2 ~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2/ 198.19.240.12:.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2/
```

## Operate

```sh
task sparks:switch PROFILE=glm
task sparks:status
task sparks:logs HOST=10.32.21.31
task sparks:down
```

The guide serves `GLM-5.3-Flash-EXL3` on spark-1 `:8888`. The profile waits for the guide's health gate and post-ready DFlash2 shape warmup, then runs an independent thinking-off coherence smoke before recording residency. TP=2 never starts automatically after a reboot.

## Live receipt

Validated on spark-1/spark-2 on 2026-08-30. NCCL logged `NET/IB` on `rocep1s0f0` with OOB/bootstrap on `enp1s0f0np0:198.19.240.11`; `/v1/models` reported the served name and 1M context; a thinking-off arithmetic/geography smoke returned a coherent exact answer. Five 400-token runs measured 63.54 tok/s median for structured decode and 25.72 tok/s median for prose, with ~0.48 s median TTFT, no NaNs, and health 200 after both benches. Cold boot reached health in 600 seconds. This boot allocated 1,362,318 KV tokens (1.36× at 1M).
