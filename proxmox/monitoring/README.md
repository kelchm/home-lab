# PVE host monitoring

Host metrics for the PVE nodes. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter, smartctl-exporter and vmagent. Identical on every node. |
| [backup-metrics/](backup-metrics/) | Backup completion samples from PVE's notification webhook, their alerts and the manual install. Installed; rollout and operator receipt evidence are tracked in #720. |
| [scrape.yaml](scrape.yaml) | Host, SMART, vmagent and deployer scrape jobs. |
| [.doco-cd.yml](.doco-cd.yml) | The deployment every node's doco-cd applies. |
| [../platform/doco-cd](../platform/doco-cd/) | The self-updating deployer and Docker daemon settings. Bootstrap and daemon configuration are applied by hand. |

Docker on a hypervisor must not manage networking. [daemon.json](../platform/doco-cd/daemon.json) turns that off and has to be in place before the Docker package is installed; with the defaults, starting the daemon alone drops traffic between bridged guests. Every container here uses host networking. The deployer also runs without Docker's default AppArmor profile, which on PVE's kernel denies the Unix socket it needs.

The deployment config includes doco-cd itself, using its native applier strategy. Migration status, manual apply, and acceptance checks are maintained in the [self-update runbook](../../docs/runbooks/doco-cd-self-update.md). Routine updates after migration need no daemon restart or networking changes.

Cluster, guest, storage and backup-coverage state comes from the Proxmox API through the in-cluster [exporter](../../kubernetes/apps/observability/pve-exporter/app/), not from these hosts.

## Drive health

Each host has one NVMe controller at `/dev/nvme0`: WD_BLACK SN770 1 TB on PVE. The stock v0.15.0 smartctl-exporter is scraped on loopback every minute. It maps only that controller, runs as root with `CAP_SYS_ADMIN`, and retains Docker's default AppArmor profile, dropped remaining capabilities, read-only filesystem and a 128 MiB memory limit. It is not privileged, but NVMe admin authority can still modify or erase the mapped drive if compromised; the device `r` mapping does not restrict ioctls to harmless commands. Adding a drive requires updating the explicit device list.

The existing **Hosts / Drive health** dashboard and shared rules use `instance`, `platform` and `device`. See the runbook for [permissions, the exporter cache limitation, and post-merge runtime verification](../../docs/runbooks/host-monitoring.md#drive-health). Runtime collection on these hosts remains to be verified after deployment.
