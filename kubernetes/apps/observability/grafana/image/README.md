# Grafana Aurora theme image

Candidate image for the homelab; production still uses stock Grafana. See the [theme runbook](../../../../../docs/runbooks/grafana-theme.md) for the implementation boundary, build, qualification, upgrade and rollback procedure.

```sh
docker build -t grafana-aurora:local .
corepack pnpm install --frozen-lockfile --ignore-scripts
corepack pnpm exec playwright install chromium
corepack pnpm test
node qualify.mjs grafana-aurora:local
```

The qualification script creates and removes its own local Grafana container and database volume. It uses generated local credentials and Grafana TestData, with no homelab datasource or production access. Reports and screenshots are saved under ignored `test-results/`.
