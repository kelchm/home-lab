# DGX Spark workloads

SparkRun is the preferred operating approach for inference on the two DGX Sparks. These hosts are outside Flux and Talos automation; merging repository changes does not deploy or restart their workloads. Hardware bring-up, networking, RDMA and fabric isolation are documented in the [bring-up runbook](../docs/runbooks/dgx-spark-bringup.md).

## SparkRun operation

Use the [Mia GLM-5.3-Flash SparkRun runbook](inference/sparkrun/README.md) for the pinned recipe, launch, status, logs, stop, validation and rollback. Keep the exact working recipe and prepared mod bundle before an upgrade, and stop a workload with the recipe that launched it.

Inspect the hosts independently of any serving recipe:

```sh
task sparks:status
```

This reports GPU utilization, host memory and running containers. Use SparkRun's recipe-specific checks to determine serving readiness; a container listing is not an API health check. Check for other GPU workloads before launching: GB10 shares its 121 GiB memory pool between CPU and GPU, and an idle GPU can still have model weights resident. Host memory, rather than the container memory limit, is the relevant capacity gate. See the [measured thermal and memory limits](../docs/dgx-spark-thermal.md).

## Monitoring

Host, GPU and inference metrics are collected by the [monitoring project](monitoring/README.md), which doco-cd deploys from `main` once a node is enrolled. It is the one part of this directory that a merge can change on a Spark.

## Retired Qwen Compose path

The standalone Qwen3.6 Compose recipe and `task sparks:deploy` are retired in favor of SparkRun. The old generic `sparks:down` and `sparks:logs` commands are also removed; use the workload's own lifecycle commands. Retirement removes the repository launch path, not host files, containers, images, model weights or caches. Copies staged under `/opt/spark-stack` may remain and are no longer maintained by this repository.

The [retirement investigation](https://github.com/kelchm/home-lab/issues/605) records the version drift and unqualified NVFP4 kernel assumptions that made the dormant recipe unsuitable as a maintained fallback. The old configuration and measurements remain in [Git history](https://github.com/kelchm/home-lab/tree/c9441c8727987f267928f9c22b31a1e6e6477c80/sparks). A future Qwen deployment should be a separately qualified SparkRun recipe with explicit runtime/model pins and current kernel settings.

Host cleanup is a separate operation. Establish which recipes and experiments own each path before removing anything; model files and caches can be shared or hardlinked. This retirement does not authorize deleting `/opt/spark-models`, `/opt/spark-cache` or other retained working data.

## Legacy DeepSeek guide

The [DeepSeek-V4-Flash-0731 guide and site overrides](inference/deepseek/) remain as historical operating material for the externally built DSpark runtime. Its build, launch and patch requirements belong to that guide. The remaining `task sparks:deepseek:logs HOST=...` and `task sparks:deepseek:down` helpers target only its `dspark-guide` containers; they do not operate SparkRun or other experiments.
