# Spark Talos trial

A throwaway two-node Talos cluster on the DGX Sparks for [#1400](https://github.com/kelchm/home-lab/issues/1400). It is not k8s-prod and Flux does not apply it. Talos runs from a USB drive in each Spark, and the DGX OS disk is only mounted read-only.

- `schematic.yaml` builds the arm64 Spark image: proprietary NVIDIA LTS driver and toolkit, Realtek firmware, and the GB10 kernel arguments. Its ID is `b4cc68af2f815f45fb7151db6dd023903bed21233e88f145f32d30228f246e58`.
- `talconfig.yaml` and `patches/` render the cluster: spark-1 as a schedulable control plane, spark-2 as a worker, at their current VLAN 21 and fabric addresses. Versions come from `../talenv.yaml`.

The Talos v1.14.5 `metal-arm64` image for this schematic was written to both drives on 2026-11-09 and verified by read-back checksum. A disk image is already an install, so Talos skips its installer and never selects another disk.

## Render

Secrets and rendered configs stay local; `.gitignore` covers them. mise points `TALOSCONFIG` at k8s-prod, so every `talosctl` command here passes `--talosconfig` explicitly.

```sh
cd talos/sparks-trial
talhelper gensecret > talsecret.yaml  # once per trial cluster
talhelper genconfig --env-file ../talenv.yaml --secret-file talsecret.yaml
```

## Boot a Spark into Talos

1. Stop inference with the durable-flush procedure in [the SparkRun README](../../sparks/inference/sparkrun/README.md), then shut DGX OS down cleanly. ext4 replays an unclean journal even on a read-only mount.
2. In UEFI setup, disable Secure Boot and boot the USB drive.
3. Talos starts in maintenance mode and shows its address on the console. If it has none, set a static address from the console's network screen.
4. Inspect before applying anything:

   ```sh
   talosctl -n <ip> get disks --insecure
   talosctl -n <ip> get links --insecure
   ```

5. Apply the node's config, then bootstrap etcd once on spark-1:

   ```sh
   talosctl apply-config --insecure -n <ip> -f clusterconfig/sparks-trial-spark-1.yaml
   talosctl --talosconfig clusterconfig/talosconfig -n 10.32.21.31 -e 10.32.21.31 bootstrap
   talosctl --talosconfig clusterconfig/talosconfig -n 10.32.21.31 -e 10.32.21.31 kubeconfig clusterconfig/
   ```

## Return to DGX OS

Power off and enable Secure Boot. The firmware then refuses the unsigned Talos image and boots DGX OS, whose boot entry is still first. If the firmware stops on the drive instead, remove it.
