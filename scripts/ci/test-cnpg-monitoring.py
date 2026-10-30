#!/usr/bin/env python3
"""Check HA query semantics and keep per-app alert expectations tied to desired instances.

The catalog-only SELECT uses portable SQL: SQLite fixture tables execute the exact
query without a database server. Production PostgreSQL/exporter acceptance is separate.
"""
import json
import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def yaml_json(path, expression="."):
    return json.loads(subprocess.check_output(["yq", "-o=json", expression, str(path)], text=True))


checks = 0
for namespace, app in [("iot", "visionect"), ("printing", "bambuddy")]:
    directory = ROOT / "kubernetes/apps" / namespace / app / "app"
    cluster = yaml_json(directory / "cluster.yaml")
    database = cluster["metadata"]["name"]
    rules = yaml_json(directory / "alerts.yaml", '.spec.groups[].rules')
    ha_rules = [rule for rule in rules if rule["alert"] == "CNPGStreamingStandbyMissing"]
    desired = cluster["spec"]["instances"] - 1
    if desired == 0:
        assert not ha_rules, f"{database}: singleton must not have a standby-loss alert"
        continue
    assert len(ha_rules) == 1, f"{database}: HA cluster needs exactly one standby-loss rule"
    threshold = re.search(r'cnpg_ha_streaming_replicas\{[^}]+\}\s*>=\s*(\d+)', ha_rules[0]["expr"])
    assert threshold and int(threshold[1]) == desired, f"{database}: alert must require spec.instances - 1 standbys"
    assert ha_rules[0]["labels"]["cnpg_cluster"] == database
    assert ha_rules[0]["labels"]["namespace"] == namespace
    monitor = yaml_json(directory / "monitoring.yaml")
    assert {"name": monitor["metadata"]["name"], "key": "queries"} in cluster["spec"]["monitoring"]["customQueriesConfigMap"]
    assert "cnpg.io/reload" in monitor["metadata"]["labels"]
    assert "./monitoring.yaml" in yaml_json(directory / "kustomization.yaml")["resources"]
    ha = yaml_json(directory / "monitoring.yaml", '.data.queries | from_yaml | .ha')
    assert ha["primary"] is True, f"{database}: standby exporter must not claim to count the primary's senders"
    assert ha["metrics"][0]["streaming_replicas"]["usage"] == "GAUGE"

    connection = sqlite3.connect(":memory:")
    connection.execute("ATTACH DATABASE ':memory:' AS pg_catalog")
    connection.execute("CREATE TABLE pg_catalog.pg_stat_replication (pid INTEGER, state TEXT, usename TEXT, application_name TEXT)")
    connection.execute("CREATE TABLE pg_catalog.pg_replication_slots (active_pid INTEGER, slot_type TEXT)")
    # A peer may be replaced or become primary. Do not pin the standby to -db-2.
    sender = (11, "streaming", "streaming_replica", f"{database}-7")
    cases = [
        ("healthy streaming", [sender], [(11, "physical")], 1),
        ("standby missing", [], [], 0),
        ("connected but catching up", [(11, "catchup", *sender[2:])], [(11, "physical")], 0),
        ("connected but starting", [(11, "startup", *sender[2:])], [(11, "physical")], 0),
        ("logical sender is not a physical standby", [sender], [(11, "logical")], 0),
        ("sender without an active physical slot", [sender], [(None, "physical")], 0),
        ("foreign cluster sender", [(11, "streaming", "streaming_replica", "other-db-2")], [(11, "physical")], 0),
        ("CNPG join base backup is not the standby", [(11, "streaming", "streaming_replica", f"{database}-2-join")], [(11, "physical")], 0),
        ("two streaming physical standbys", [sender, (12, *sender[1:3], f"{database}-8")], [(11, "physical"), (12, "physical")], 2),
    ]
    for name, senders, slots, expected in cases:
        connection.execute("DELETE FROM pg_catalog.pg_stat_replication")
        connection.execute("DELETE FROM pg_catalog.pg_replication_slots")
        connection.executemany("INSERT INTO pg_catalog.pg_stat_replication VALUES (?, ?, ?, ?)", senders)
        connection.executemany("INSERT INTO pg_catalog.pg_replication_slots VALUES (?, ?)", slots)
        actual = connection.execute(ha["query"]).fetchone()[0]
        assert actual == expected, f"{database}: {name}: got {actual}, expected {expected}"
        checks += 1
    connection.close()
print(f"CNPG monitoring: {checks} query cases passed; alert thresholds match desired instances and query wiring.")
