"""Load an nftables policy into a throwaway pair of network namespaces and report who reaches which port.

Needs root, nft and ip. A host namespace listens on each port, over IPv4 and IPv6, and loads the
policy twice, then a competing table that accepts everything first; a LAN namespace on the same
link connects from each source address. Prints one JSON object with every connection's outcome, the
policy's tables, whether the second load left the same ruleset as the first, and the policy's
per-element counters.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SERVER = r"""
import socket, sys, threading
listeners = []
for port in map(int, sys.argv[1:]):
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("::", port))
    s.listen(64)
    listeners.append(s)
def serve(s):
    while True:
        s.accept()[0].close()
for s in listeners:
    threading.Thread(target=serve, args=(s,), daemon=True).start()
print("ready", flush=True)
sys.stdin.read()
"""

CLIENT = r"""
import json, socket, sys
results = []
for src, dst, port in json.loads(sys.argv[1]):
    s = socket.socket(socket.AF_INET6 if ":" in dst else socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        if src:
            s.bind((src, 0))
        s.connect((dst, port))
        outcome = "connected"
    except socket.timeout:
        outcome = "timeout"
    except OSError as error:
        outcome = type(error).__name__
    finally:
        s.close()
    results.append([src, dst, port, outcome])
print(json.dumps(results))
"""

# Another table that accepts every port first, as Docker's or a hand-loaded rule might.
COMPETING = """
table inet competing {
    chain input {
        type filter hook input priority -100; policy accept;
        accept
    }
}
"""

HOST6, LAN6 = "fd00::1", "fd00::2"


def run(*command: str, input: str | None = None) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, input=input).stdout


def connect(namespace: str, specs: list) -> list:
    return json.loads(run("ip", "netns", "exec", namespace, sys.executable, "-c", CLIENT, json.dumps(specs)))


def counters(ruleset: dict) -> dict:
    found = {}
    for item in ruleset["nftables"]:
        for element in item.get("set", {}).get("elem", []):
            if isinstance(element, dict) and "counter" in element.get("elem", {}):
                found[element["elem"]["val"]] = element["elem"]["counter"]["packets"]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy")
    parser.add_argument("--host", required=True, help="the host's IPv4 address")
    parser.add_argument("--port", type=int, action="append", required=True)
    parser.add_argument("--source", action="append", required=True, help="a LAN source address")
    args = parser.parse_args()

    host, lan = f"api-access-host-{os.getpid()}", f"api-access-lan-{os.getpid()}"
    server = None
    try:
        run("ip", "netns", "add", host)
        run("ip", "netns", "add", lan)
        run("ip", "link", "add", "veth-host", "netns", host, "type", "veth", "peer", "name", "veth-lan", "netns", lan)
        run("ip", "-n", host, "addr", "add", f"{args.host}/8", "dev", "veth-host")
        run("ip", "-n", host, "addr", "add", f"{HOST6}/64", "dev", "veth-host", "nodad")
        for source in args.source:
            run("ip", "-n", lan, "addr", "add", f"{source}/8", "dev", "veth-lan")
        run("ip", "-n", lan, "addr", "add", f"{LAN6}/64", "dev", "veth-lan", "nodad")
        for namespace, device in ((host, "veth-host"), (lan, "veth-lan")):
            run("ip", "-n", namespace, "link", "set", "lo", "up")
            run("ip", "-n", namespace, "link", "set", device, "up")

        server = subprocess.Popen(["ip", "netns", "exec", host, sys.executable, "-c", SERVER, *map(str, args.port)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        if server.stdout.readline().strip() != "ready":
            raise RuntimeError("the listener did not start")
        loaded = []
        for _ in range(2):
            run("ip", "netns", "exec", host, "nft", "-f", args.policy)
            loaded.append(run("ip", "netns", "exec", host, "nft", "list", "ruleset"))

        lan_specs = [[source, args.host, port] for source in args.source for port in args.port]
        lan_specs += [[LAN6, HOST6, port] for port in args.port]
        host_specs = [[None, address, port] for address in ("127.0.0.1", args.host, "::1") for port in args.port]
        ruleset = json.loads(run("ip", "netns", "exec", host, "nft", "-j", "list", "ruleset"))
        run("ip", "netns", "exec", host, "nft", "-f", "-", input=COMPETING)
        result = {
            "lan": connect(lan, lan_specs),
            "host": connect(host, host_specs),
            "tables": [[item["table"]["family"], item["table"]["name"]]
                       for item in ruleset["nftables"] if "table" in item],
            "reload_identical": loaded[0] == loaded[1],
        }
        result["counters"] = counters(json.loads(run("ip", "netns", "exec", host, "nft", "-j", "list", "ruleset")))
        print(json.dumps(result))
        return 0
    finally:
        if server:
            server.kill()
            server.wait()
        for namespace in (host, lan):
            subprocess.run(["ip", "netns", "delete", namespace], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
