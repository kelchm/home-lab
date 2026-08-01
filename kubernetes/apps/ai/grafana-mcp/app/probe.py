"""Exercise Grafana MCP and optionally verify MetaMCP's session policy.

The scheduled probe uses the direct Grafana MCP Service. The same script can
exercise the authenticated MetaMCP endpoint during rollout by overriding
MCP_SCHEME/HOST/PORT/PATH, MCP_TOOL_NAME, and MCP_API_KEY.
"""

import http.client
import json
import os
import sys


MCP_SCHEME = os.getenv("MCP_SCHEME", "http")
MCP_HOST = os.getenv("MCP_HOST", "grafana-mcp.ai.svc.cluster.local")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
MCP_TOOL_NAME = os.getenv("MCP_TOOL_NAME", "list_datasources")
MCP_API_KEY = os.getenv("MCP_API_KEY")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER")

BASE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
if MCP_API_KEY:
    BASE_HEADERS["X-API-Key"] = MCP_API_KEY
if MCP_HOST_HEADER:
    BASE_HEADERS["Host"] = MCP_HOST_HEADER


def parse_json_or_sse(body):
    if not body:
        return None
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    return json.loads(body)


def new_connection(scheme, host, port):
    connection_type = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    return connection_type(host, port, timeout=15)


def rpc(connection, payload, session_id=None, expected=(200,)):
    headers = dict(BASE_HEADERS)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    connection.request("POST", MCP_PATH, json.dumps(payload), headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    if response.status not in expected:
        raise RuntimeError(f"HTTP {response.status}: {body[:500]}")
    return response, parse_json_or_sse(body)


def check_session_lifetime():
    host = os.getenv("SESSION_LIFETIME_HOST")
    if not host:
        return None

    scheme = os.getenv("SESSION_LIFETIME_SCHEME", "http")
    port = int(os.getenv("SESSION_LIFETIME_PORT", "12008"))
    path = os.getenv(
        "SESSION_LIFETIME_PATH",
        "/trpc/frontend.config.getSessionLifetime?input=%7B%22json%22%3Anull%7D",
    )
    expected = int(os.environ["EXPECTED_SESSION_LIFETIME_MS"])
    connection = new_connection(scheme, host, port)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        if response.status != 200:
            raise RuntimeError(
                f"MetaMCP session-policy check returned HTTP {response.status}: {body[:500]}"
            )
        value = json.loads(body).get("result", {}).get("data")
        if isinstance(value, dict) and "json" in value:
            value = value["json"]
        if value != expected:
            raise RuntimeError(
                f"MetaMCP SESSION_LIFETIME is {value!r}; expected {expected} ms"
            )
        return value
    finally:
        connection.close()


session_lifetime = check_session_lifetime()
connection = new_connection(MCP_SCHEME, MCP_HOST, MCP_PORT)
session_id = None
try:
    response, initialized = rpc(
        connection,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "grafana-mcp-functional-probe",
                    "version": "1.0",
                },
            },
        },
    )
    session_id = response.getheader("Mcp-Session-Id")
    if not session_id or initialized.get("error"):
        raise RuntimeError(f"MCP initialize failed: {initialized}")

    rpc(
        connection,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id,
        expected=(200, 202),
    )
    _, result = rpc(
        connection,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": MCP_TOOL_NAME, "arguments": {}},
        },
        session_id,
    )
    if result.get("error") or result.get("result", {}).get("isError"):
        raise RuntimeError(f"{MCP_TOOL_NAME} failed: {result}")

    content = result.get("result", {}).get("content", [])
    payload = json.loads(content[0]["text"]) if content else {}
    datasources = payload.get("datasources")
    if not isinstance(datasources, list):
        raise RuntimeError(f"Grafana returned no datasource list: {result}")
    suffix = (
        f", session lifetime {session_lifetime} ms"
        if session_lifetime is not None
        else ""
    )
    print(
        f"Grafana MCP functional probe passed via {MCP_HOST} "
        f"({len(datasources)} datasources{suffix})"
    )
finally:
    active_error = sys.exc_info()[0] is not None
    if session_id:
        try:
            headers = dict(BASE_HEADERS)
            headers["Mcp-Session-Id"] = session_id
            connection.request("DELETE", MCP_PATH, headers=headers)
            response = connection.getresponse()
            response.read()
            if response.status != 200:
                raise RuntimeError(
                    f"MCP session cleanup returned HTTP {response.status}"
                )
        except Exception as cleanup_error:
            if not active_error:
                raise
            print(f"Session cleanup also failed: {cleanup_error}", file=sys.stderr)
    connection.close()
