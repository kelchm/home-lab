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
    slots = yaml_json(directory / "monitoring.yaml", '.data.queries | from_yaml | .ha_slots')
    assert slots["primary"] is True
    assert slots["metrics"][0]["invalidated"]["usage"] == "GAUGE"
    # This selector follows CNPG's default HA slot prefix and instance-name mapping.
    assert cluster["spec"].get("replicationSlots", {}).get("highAvailability", {}).get("slotPrefix", "_cnpg_") == "_cnpg_"
    invalidated_rules = [rule for rule in rules if rule["alert"] == "CNPGHAReplicationSlotInvalidated"]
    assert len(invalidated_rules) == 1
    assert invalidated_rules[0]["labels"]["cnpg_cluster"] == database
    assert invalidated_rules[0]["labels"]["namespace"] == namespace
    connection.execute("ALTER TABLE pg_catalog.pg_replication_slots ADD COLUMN slot_name TEXT")
    connection.execute("ALTER TABLE pg_catalog.pg_replication_slots ADD COLUMN temporary INTEGER")
    connection.execute("ALTER TABLE pg_catalog.pg_replication_slots ADD COLUMN invalidation_reason TEXT")
    slot_name = "_cnpg_" + database.replace("-", "_") + "_7"
    valid = (11, "physical", slot_name, False, None)
    invalid = (*valid[:4], "wal_removed")
    slot_cases = [
        ("valid active HA slot", [valid], 0),
        ("invalidated active HA slot still streaming on PG17", [invalid], 1),
        ("invalidated inactive HA slot on PG18", [(None, *invalid[1:])], 1),
        ("no slots", [], 0),
        ("invalidated logical slot", [(11, "logical", *invalid[2:])], 0),
        ("temporary base-backup slot", [(11, "physical", "pg_basebackup_123", True, "wal_removed")], 0),
        ("temporary slot with HA-like name", [(*invalid[:3], True, "wal_removed")], 0),
        ("unrelated physical slot", [(None, "physical", "external_backup", False, "wal_removed")], 0),
        ("foreign cluster's HA slot", [(None, "physical", "_cnpg_other_db_2", False, "wal_removed")], 0),
        ("underscores in prefix are literal", [(None, "physical", slot_name.replace("_", "x"), False, "wal_removed")], 0),
        ("one valid and two invalidated slots", [valid, invalid, (None, "physical", slot_name + "8", False, "wal_removed")], 2),
    ]
    for name, rows, expected in slot_cases:
        connection.execute("DELETE FROM pg_catalog.pg_replication_slots")
        connection.executemany("INSERT INTO pg_catalog.pg_replication_slots VALUES (?, ?, ?, ?, ?)", rows)
        actual = connection.execute(slots["query"]).fetchone()[0]
        assert actual == expected, f"{database}: {name}: got {actual}, expected {expected}"
        checks += 1
    connection.close()
print(f"CNPG monitoring: {checks} query cases passed; alert thresholds match desired instances and query wiring.")
