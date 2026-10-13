# PVE host monitoring

Host metrics for the PVE nodes. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter and vmagent. Identical on every node. |
| [backup-metrics/](backup-metrics/) | Backup completion samples from PVE's notification webhook, their alerts and the manual install. Installed; rollout and operator receipt evidence are tracked in #720. |
| [scrape.yaml](scrape.yaml) | Host, vmagent and deployer scrape jobs. |
| [.doco-cd.yml](.doco-cd.yml) | The deployment every node's doco-cd applies. |
| [../platform/doco-cd](../platform/doco-cd/) | The self-updating deployer and Docker daemon settings. Bootstrap and daemon configuration are applied by hand. |

Docker on a hypervisor must not manage networking. [daemon.json](../platform/doco-cd/daemon.json) turns that off and has to be in place before the Docker package is installed; with the defaults, starting the daemon alone drops traffic between bridged guests. Every container here uses host networking. The deployer also runs without Docker's default AppArmor profile, which on PVE's kernel denies the Unix socket it needs.

The deployment config includes doco-cd itself, using its native applier strategy. Migration status, manual apply, and acceptance checks are maintained in the [self-update runbook](../../docs/runbooks/doco-cd-self-update.md). Routine updates after migration need no daemon restart or networking changes.

Cluster, guest, storage and backup-coverage state comes from the Proxmox API through the in-cluster [exporter](../../kubernetes/apps/observability/pve-exporter/app/), not from these hosts.
