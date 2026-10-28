"""spark-1's API access policy, loaded into network namespaces with the real nft."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOST_FILES = Path(__file__).resolve().parents[1] / "host"
POLICY = HOST_FILES / "sparkrun-api-access.nft"
UNIT = HOST_FILES / "sparkrun-api-access.service"
HELPER = Path(__file__).with_name("api_access_netns.py")
HOST = "10.32.21.31"
PORT, OTHER_PORT = 8888, 9100
# Multiple Main devices, including subnet edges, must work without individual pins.
CLIENTS = ["10.32.10.1", "10.32.10.244", "10.32.10.254", "10.32.21.100"]
COUNTER_ENTRIES = {"10.32.10.0/24", "10.32.21.100"}
# Unlisted sources: 10.32.21.101, spark-2 and the NAS's storage address.
REFUSED = ["10.32.21.101", "10.32.21.32", "10.32.25.5", "10.32.9.254", "10.32.11.1", "10.32.90.1", "10.32.99.1"]
SBIN = os.pathsep.join([os.environ.get("PATH", ""), "/usr/sbin", "/sbin"])


def as_root() -> list[str]:
    """The prefix that runs a command as root, or a skip where none is available; CI must have one."""
    reason = None
    if platform.system() != "Linux":
        reason = "network namespaces need Linux"
    elif not (shutil.which("nft", path=SBIN) and shutil.which("ip", path=SBIN)):
        reason = "nft and ip are not installed"
    elif os.geteuid() == 0:
        return []
    elif subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        reason = "needs root or passwordless sudo"
    if reason:
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)
    return ["sudo", "-n"]


@pytest.fixture(scope="module")
def result():
    command = [*as_root(), sys.executable, str(HELPER), str(POLICY), "--host", HOST,
               "--port", str(PORT), "--port", str(OTHER_PORT)]
    for source in CLIENTS + REFUSED:
        command += ["--source", source]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def outcomes(result, side, port):
    return {(src, dst): outcome for src, dst, p, outcome in result[side] if p == port}


def test_only_listed_clients_reach_the_api_port_from_the_lan(result):
    reached = {src for (src, _), outcome in outcomes(result, "lan", PORT).items() if outcome == "connected"}
    assert reached == set(CLIENTS)
    dropped = {src for (src, _), outcome in outcomes(result, "lan", PORT).items() if outcome == "timeout"}
    assert dropped == set(REFUSED) | {"fd00::2"}


def test_loopback_reaches_the_api_port_by_any_local_address(result):
    assert set(outcomes(result, "host", PORT).values()) == {"connected"}


def test_other_ports_are_untouched(result):
    assert set(outcomes(result, "lan", OTHER_PORT).values()) == {"connected"}


def test_reloading_replaces_the_table(result):
    assert result["tables"] == [["inet", "sparkrun_api_access"]]
    assert result["reload_identical"]


def test_each_allowed_network_or_host_counts_its_packets(result):
    assert set(result["counters"]) == COUNTER_ENTRIES
    assert all(packets > 0 for packets in result["counters"].values())


def test_unit_loads_and_removes_the_policys_table():
    unit = UNIT.read_text()
    table = re.search(r"^table (inet \w+) \{", POLICY.read_text(), re.M).group(1)
    assert re.search(r"^ExecStart=/usr/sbin/nft -f /etc/sparkrun-api-access\.nft$", unit, re.M)
    assert re.search(r"^ExecReload=/usr/sbin/nft -f /etc/sparkrun-api-access\.nft$", unit, re.M)
    assert re.search(rf"^ExecStop=-/usr/sbin/nft delete table {table}$", unit, re.M)
    assert re.search(r"^Before=docker\.service$", unit, re.M)
