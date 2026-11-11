"""Exercise Grafana MCP with a real tool call.

The scheduled probe follows the HTTPS admin gateway and MCPHub homelab-read
route used by clients. Its dedicated bearer key permits only list_datasources.
"""

import http.client
import json
import os
import sys


MCP_SCHEME = os.getenv("MCP_SCHEME", "https")
MCP_HOST = os.getenv("MCP_HOST", "mcphub.home.kelch.io")
MCP_PORT = int(os.getenv("MCP_PORT", "443"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp/homelab-read")
MCP_TOOL_NAME = os.getenv("MCP_TOOL_NAME", "grafana__list_datasources")
MCP_API_KEY = os.getenv("MCP_API_KEY")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER")

BASE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
if MCP_API_KEY:
    BASE_HEADERS["Authorization"] = f"Bearer {MCP_API_KEY}"
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


def rpc(connection, payload, session_id=None, expected=(200,), protocol_version=None):
    headers = dict(BASE_HEADERS)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    connection.request("POST", MCP_PATH, json.dumps(payload), headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    if response.status not in expected:
        raise RuntimeError(f"MCP request returned HTTP {response.status}")
    return response, parse_json_or_sse(body)


def main():
    if not MCP_API_KEY:
        raise RuntimeError("MCP_API_KEY is required for the gateway probe")
    connection = new_connection(MCP_SCHEME, MCP_HOST, MCP_PORT)
    session_id = None
    protocol_version = "2025-06-18"
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
        # A valid initialization can be stateless, without a session ID.
        if (
            not isinstance(initialized, dict)
            or initialized.get("error")
            or not isinstance(initialized.get("result"), dict)
        ):
            raise RuntimeError("MCP initialize failed")

        protocol_version = initialized["result"].get("protocolVersion", "2025-06-18")
        rpc(
            connection,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
            expected=(200, 202),
            protocol_version=protocol_version,
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
            protocol_version=protocol_version,
        )
        if (
            not isinstance(result, dict)
            or result.get("error")
            or not isinstance(result.get("result"), dict)
            or result["result"].get("isError")
        ):
            raise RuntimeError(f"{MCP_TOOL_NAME} failed")

        content = result.get("result", {}).get("content", [])
        payload = json.loads(content[0]["text"]) if content else {}
        datasources = payload.get("datasources")
        if not isinstance(datasources, list):
            raise RuntimeError("Grafana returned no datasource list")
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            if session_id:
                try:
                    headers = dict(BASE_HEADERS)
                    headers["Mcp-Session-Id"] = session_id
                    headers["MCP-Protocol-Version"] = protocol_version
                    connection.request("DELETE", MCP_PATH, headers=headers)
                    response = connection.getresponse()
                    response.read()
                    if response.status not in (200, 204):
                        raise RuntimeError(
                            f"MCP session cleanup returned HTTP {response.status}"
                        )
                except Exception as cleanup_error:
                    if not active_error:
                        raise
                    print(f"Session cleanup also failed: {cleanup_error}", file=sys.stderr)
        finally:
            connection.close()
    print(
        f"Grafana MCP functional probe passed via {MCP_HOST} "
        f"({len(datasources)} datasources)"
    )


if __name__ == "__main__":
    main()
