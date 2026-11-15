#!/usr/bin/env python3
"""Opt-in MCP name qualification against installed clients; no model turn is sent.

By default use synthetic tools. --catalog accepts a private JSON list of
{connection, backend, tool} records, or an object containing that list at catalog.
Raw catalogs stay in the private temporary directory, never in the summary.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time


def serve(path: str) -> None:
    names = json.loads(Path(path).read_text())
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "credential-free-name-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": n, "description": "Local fixture", "inputSchema": {"type": "object", "properties": {}}} for n in names]}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unsupported fixture method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def clean_env() -> dict[str, str]:
    # Retain normal filesystem paths, but forward no provider credentials.
    return {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SHELL", "TMPDIR", "LANG"} or k.startswith("LC_")}


def command_for(folder: Path, label: str, names: list[str]) -> dict:
    path = folder / f"{label}.json"
    path.write_text(json.dumps(names))
    return {"command": sys.executable, "args": [str(Path(__file__).resolve()), "--serve", str(path)]}


def projected_catalog(rows: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for row in rows:
        name = row["backend"] + "-" + row["tool"]
        connection = row["connection"]
        assert re.fullmatch(r"[A-Za-z0-9-][A-Za-z0-9_-]*", name) and "__" not in name, "Grok-incompatible tool component"
        assert len(f"mcp__{connection}__{name}") <= 64, "Claude/Codex qualified name exceeds 64 characters"
        groups.setdefault(connection, []).append(name)
    for names in groups.values():
        assert len(names) == len(set(names)), "Hyphen projection collides within a group"
    return groups


def grok_case(grok: str, folder: Path, label: str, connection: str, names: list[str], skipped: int) -> dict:
    home = folder / label
    home.mkdir()
    fixture = command_for(home, "tools", names)
    config = "[cli]\nauto_update=false\nuse_leader=false\nsession_registry=false\n"
    for vendor in ["claude", "cursor", "codex"]:
        config += f"[compat.{vendor}]\nmcps=false\nhooks=false\nskills=false\nagents=false\nrules=false\n"
    config += f"[mcp_servers.{json.dumps(connection)}]\ncommand={json.dumps(fixture['command'])}\nargs={json.dumps(fixture['args'])}\n"
    (home / "config.toml").write_text(config)
    env = clean_env() | {"GROK_HOME": str(home), "GROK_DISABLE_AUTOUPDATER": "1"}
    run = subprocess.run([grok, "mcp", "doctor", "--json", connection], cwd=home, env=env, capture_output=True, text=True, timeout=30)
    server = next(s for s in json.loads(run.stdout)["servers"] if s["name"] == connection)
    check = next(c for c in server["checks"] if "tool" in c["label"])
    match = re.search(r"(\d+) skipped by session admission", check["label"])
    actual_skipped = int(match[1]) if match else 0
    assert actual_skipped == skipped, f"Unexpected Grok admission outcome for {label}"
    assert server["healthy"] == (skipped == 0), f"Transport/admission failure for {label}"
    assert run.returncode == (1 if skipped else 0), f"Unexpected doctor exit for {label}"
    return {"case": label, "listed": len(names), "skipped": actual_skipped}


def control_client(command: list[str], folder: Path, initial: dict, on_message, *, client_env: dict[str, str]) -> object:
    process = subprocess.Popen(command, cwd=folder, env=clean_env() | client_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    send(initial)
    deadline = time.monotonic() + 30
    answer = None
    try:
        while answer is None and time.monotonic() < deadline and process.poll() is None:
            for key, _ in selector.select(timeout=0.1):
                line = key.fileobj.readline()
                if not line or key.fileobj is process.stderr:
                    continue
                answer = on_message(json.loads(line), send)
                if answer is not None:
                    break
        assert answer is not None, "Local client discovery did not finish before the deadline"
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        selector.close()
    return answer


def claude_check(claude: str, folder: Path, groups: dict[str, list[str]]) -> dict:
    client_home = folder / "claude-home"
    client_home.mkdir(mode=0o700)
    config = folder / "claude-mcp.json"
    config.write_text(json.dumps({"mcpServers": {name: command_for(folder, f"claude-{i}", names) for i, (name, names) in enumerate(groups.items())}}))

    def on_message(message, send):
        response = message.get("response", {})
        if response.get("request_id") == "initialize":
            send({"type": "control_request", "request_id": "status", "request": {"subtype": "mcp_status"}})
        elif response.get("request_id") == "status":
            servers = response.get("response", {}).get("mcpServers", [])
            if servers and all(s["status"] == "connected" for s in servers):
                assert {s["name"] for s in servers} == set(groups)
                for server in servers:
                    assert {t["name"] for t in server["tools"]} == set(groups[server["name"]])
                return {s["name"]: len(s["tools"]) for s in servers}
            time.sleep(0.2)
            send({"type": "control_request", "request_id": "status", "request": {"subtype": "mcp_status"}})
        return None

    return control_client([claude, "--bare", "--strict-mcp-config", "--mcp-config", str(config), "--setting-sources", "", "--input-format", "stream-json", "--output-format", "stream-json", "--no-session-persistence", "--verbose", "--print"], folder, {"type": "control_request", "request_id": "initialize", "request": {"subtype": "initialize", "hooks": {}}}, on_message, client_env={"CLAUDE_CONFIG_DIR": str(client_home)})


def codex_check(codex: str, folder: Path, groups: dict[str, list[str]]) -> dict:
    # An empty client home prevents loading real MCP headers or writing real state.
    # Each discovery request selects only its fixture; no thread or model turn starts.
    client_home = folder / "codex-home"
    client_home.mkdir(mode=0o700)
    aliases = {name: f"fixture617-{i}" for i, name in enumerate(groups)}
    entries = []
    for name, names in groups.items():
        cfg = command_for(folder, aliases[name], names)
        entries.append(json.dumps(aliases[name]) + "={command=" + json.dumps(cfg["command"]) + ",args=" + json.dumps(cfg["args"]) + "}")
    config = "mcp_servers={" + ",".join(entries) + "}"
    pending = {i + 2: name for i, name in enumerate(groups)}
    counts = {}

    def on_message(message, send):
        if message.get("id") == 1:
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            for rid, name in pending.items():
                send({"jsonrpc": "2.0", "id": rid, "method": "mcpServerStatus/list", "params": {"serverName": aliases[name], "detail": "toolsAndAuthOnly"}})
        elif message.get("id") in pending:
            name = pending.pop(message["id"])
            servers = message.get("result", {}).get("data", [])
            assert len(servers) == 1 and servers[0]["name"] == aliases[name], "Codex fixture discovery failed"
            assert not servers[0].get("toolsError") and set(servers[0]["tools"]) == set(groups[name]), "Codex catalog differs"
            counts[name] = len(servers[0]["tools"])
            if not pending:
                return counts
        return None

    return control_client([codex, "app-server", "-c", config, "-c", "features.apps=false"], folder, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "mcp-name-fixture", "version": "1"}, "capabilities": {"experimentalApi": True}}}, on_message, client_env={"CODEX_HOME": str(client_home)})


def hermes_check(path: Path, groups: dict[str, list[str]]) -> dict:
    # Run the pinned source's pure functions, without importing runtime/profile state.
    tree = ast.parse(path.read_text())
    functions = {"sanitize_mcp_name_component", "mcp_prefixed_tool_name"}
    constants = {"MCP_TOOL_NAME_PREFIX", "_MCP_TOOL_NAME_MAX_LENGTH", "_MCP_TOOL_NAME_HASH_LENGTH", "_clamped_names_warned"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in constants for t in targets):
                selected.append(node)
    namespace = {"re": re, "hashlib": hashlib, "logger": logging.getLogger("hermes-name-fixture")}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    convert = namespace["mcp_prefixed_tool_name"]
    names = [convert(connection, tool) for connection, tools in groups.items() for tool in tools]
    assert len(set(names)) == len(names), "Hermes sanitized names collide"
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n) for n in names)
    # The managed Hermes connection uses a longer name than the operator clients.
    hn_names = [convert("mcphub_hacker_news", n) for n in groups.get("hacker-news", [])]
    assert len(set(hn_names)) == len(hn_names)
    return {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "qualified": len(names), "managed_hacker_news": len(hn_names), "scope": "source naming functions, not running Hermes acceptance"}


def main() -> None:
    if not __debug__:
        raise SystemExit("Qualification requires assertions; run without Python -O or -OO.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", help=argparse.SUPPRESS)
    parser.add_argument("--grok", default=shutil.which("grok"))
    parser.add_argument("--claude", help="Also exercise Claude's local control-protocol discovery")
    parser.add_argument("--codex", help="Also exercise Codex's local app-server discovery")
    parser.add_argument("--hermes-schema", type=Path, help="Pinned tools/mcp_tool_schema.py; naming functions only")
    parser.add_argument("--catalog", type=Path, help="Private catalog; no raw names are emitted in the summary")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    if not args.grok:
        parser.error("Grok must be installed or supplied with --grok")
    rows = [{"connection": "homelab-read", "backend": "grafana", "tool": "query_prometheus"}, {"connection": "homelab-read", "backend": "kubernetes", "tool": "pods_list"}, {"connection": "hacker-news", "backend": "hackernews", "tool": "hn_get_stories"}]
    if args.catalog:
        data = json.loads(args.catalog.read_text())
        rows = data["catalog"] if isinstance(data, dict) else data
    groups = projected_catalog(rows)
    # A separator does not make a projection injective for arbitrary future backends.
    try:
        projected_catalog([{"connection": "c", "backend": "a-b", "tool": "c"}, {"connection": "c", "backend": "a", "tool": "b-c"}])
    except AssertionError:
        pass
    else:
        raise AssertionError("Collision guard accepted distinct tuples with the same name")
    cases = [("plain", "fixture", ["query_prometheus"], 0), ("nested-single", "fixture", ["grafana__query_prometheus"], 1), ("nested-multi", "fixture", ["grafana__query_prometheus", "kubernetes__pods_list"], 2), ("hyphen", "fixture", ["grafana-query_prometheus", "kubernetes-pods_list"], 0), ("single-underscore", "fixture", ["grafana_query_prometheus"], 0), ("leading-underscore", "fixture", ["_leading"], 1), ("punctuation", "fixture", ["with.dot", "with:colon", "with/slash", "with space", "naïve"], 5), ("connection-nested", "fixture__nested", ["tool"], 1), ("length-256", "fixture", ["x" * 247], 0), ("length-257", "fixture", ["x" * 248], 1), ("doctor-duplicate-limit", "fixture", ["same-tool", "same-tool"], 0)]
    with tempfile.TemporaryDirectory(prefix="mcp-name-qualification-") as temporary:
        folder = Path(temporary)
        summary = {"grok_version": subprocess.check_output([args.grok, "--version"], text=True).strip(), "model_turns": 0}
        summary["name_cases"] = [grok_case(args.grok, folder, *case) for case in cases]
        summary["grok_catalog"] = {name: grok_case(args.grok, folder, f"catalog-{i}", name, names, 0)["listed"] for i, (name, names) in enumerate(groups.items())}
        summary["max_claude_codex_qualified_length"] = max(len(f"mcp__{c}__{n}") for c, names in groups.items() for n in names)
        if args.claude:
            summary["claude_version"] = subprocess.check_output([args.claude, "--version"], text=True).strip()
            summary["claude_catalog"] = claude_check(args.claude, folder, groups)
        if args.codex:
            summary["codex_version"] = subprocess.check_output([args.codex, "--version"], text=True).strip()
            summary["codex_catalog"] = codex_check(args.codex, folder, groups)
        if args.hermes_schema:
            summary["hermes"] = hermes_check(args.hermes_schema, groups)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
