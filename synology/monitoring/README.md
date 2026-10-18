# NAS monitoring

Host, disk, RAID and UPS metrics for Athena. Enrollment, verification and removal are in the [host monitoring runbook](../../docs/runbooks/host-monitoring.md#nas).

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | node-exporter, snmp_exporter, vmagent, and the one-shot `publish` that copies the two config files below into `/volume1/docker/monitoring/config`. |
| [scrape.yaml](scrape.yaml) | Host, SNMP and vmagent scrape jobs. |
| [snmp.yml](snmp.yml) | The SNMP credentials; the `synology` module itself is the stock one shipped in the image. |

Every container uses host networking and listens only on `127.0.0.1`. The SNMP source is DSM's own `snmpd`, which DSM runs bound to loopback with the read-only community `syno` with the SNMP service in Control Panel disabled. Keep it disabled; this project does not need it, and with it off nothing answers SNMP beyond loopback.

The two sources overlap, and each is used for what it reports correctly:

- **node-exporter** for CPU, memory, interfaces, disk I/O, NFS server counters and filesystem use. It mounts `/` without `rslave`, which Docker refuses because DSM's root is not a shared mount. Container Manager re-mounts every share under `all_shares`, so those mount points are excluded. DSM's system arrays `md0` and `md1` span all eight bays and always report fewer active disks than required, so `node_md_*` does not show RAID health. The 4.4 kernel has no pressure stall information.
- **snmp_exporter** for DSM's own view: system, power and fan status, per-disk status, health, temperature and SMART attributes, storage pool and volume status, and the UPS DSM is attached to. A walk takes under a second and yields about 1,600 series, most of them SMART attributes.

snmp_exporter v0.30.1 hex-encodes `diskID`; [scrape.yaml](scrape.yaml) decodes it to `Disk N`. The upstream fix ([prometheus/snmp_exporter#1603](https://github.com/prometheus/snmp_exporter/pull/1603)) emits the same value, so that rule matches nothing once a release includes it.

vmagent rereads its scrape file every minute. snmp_exporter reads [snmp.yml](snmp.yml) only at start, so after changing it run `docker restart monitoring-snmp-exporter-1` on Athena.

doco-cd on Athena publishes no metrics port, so deployment failures there are not alerted.

## Drive health

Athena keeps DSM SNMP as its disk collector. All eight Seagate ST14000NM001G-2KJ103 14 TB HDDs expose health, temperature and SMART attributes, including the two uninitialized disks. Shared recording rules provide `platform="synology"`, `instance="nas"`, `device="Disk N"` to the existing **Hosts / Drive health** dashboard. Rules alert on reallocated, pending and offline uncorrectable sectors by numeric attribute ID, as well as DSM failed health; the DSM dashboard, health-warning, temperature, RAID, hardware and UPS rules remain.

This avoids a second container with direct disk authority. The live series are sufficient for these health signals but are not lossless raw SMART: remaining life is unsupported (`-1`), and a large packed command-timeout value was clamped. Native diagnostics require DSM's `smartctl -d sat`. Evidence, limitations and verification queries are in the [drive-health runbook](../../docs/runbooks/host-monitoring.md#drive-health).
