#!/usr/bin/env python3
"""Exercise the gateway probe's authentication and MCP failure handling offline."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "probe", ROOT / "kubernetes/apps/ai/grafana-mcp/app/probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Response:
    def __init__(self, status, payload=None, session=None):
        self.status = status
        self.payload = payload
        self.session = session

    def read(self):
        if self.payload is None:
            return b""
        return ("event: message\ndata: " + json.dumps(self.payload) + "\n\n").encode()

    def getheader(self, name):
        return self.session if name == "Mcp-Session-Id" else None


class Gateway:
    def __init__(self, status=200, result=None, session="session-1", cleanup=204):
        self.status = status
        self.result = result or {
            "jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": '{"datasources": []}'}]},
        }
        self.session = session
        self.cleanup = cleanup
        self.calls = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        assert path == "/mcp/homelab-read"
        assert headers["Authorization"] == "Bearer fixture-key"
        assert "X-API-Key" not in headers
        payload = json.loads(body) if body else {}
        self.calls.append((method, payload.get("method")))
        if method == "DELETE":
            self.response = Response(self.cleanup)
            return
        if payload["method"] == "initialize":
            self.response = Response(200, {"result": {"protocolVersion": "2025-06-18"}}, self.session)
            return
        assert headers["MCP-Protocol-Version"] == "2025-06-18"
        if self.session:
            assert headers["Mcp-Session-Id"] == self.session
        if payload["method"] == "notifications/initialized":
            self.response = Response(202)
        else:
            assert payload["params"] == {"name": "grafana__list_datasources", "arguments": {}}
            self.response = Response(self.status, self.result)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ProbeTests(unittest.TestCase):
    def run_probe(self, gateway):
        with (
            patch.object(probe, "MCP_API_KEY", "fixture-key"),
            patch.dict(probe.BASE_HEADERS, {"Authorization": "Bearer fixture-key"}),
            patch.object(probe, "new_connection", return_value=gateway) as connect,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            try:
                probe.main()
            finally:
                connect.assert_called_once_with("https", "mcphub.home.kelch.io", 443)
                self.assertTrue(gateway.closed)

    def test_gateway_session_and_stateless_success(self):
        for session in ("session-1", None):
            with self.subTest(session=session):
                gateway = Gateway(session=session)
                self.run_probe(gateway)
                self.assertIn(("POST", "tools/call"), gateway.calls)
                self.assertEqual(("DELETE", None) in gateway.calls, session is not None)

    def test_broken_route_and_rejected_key_fail(self):
        for status in (401, 403, 404, 502):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                    self.run_probe(Gateway(status=status))

    def test_tool_errors_and_invalid_response_fail(self):
        for result in (
            {"error": {"code": -32601, "message": "Tool unavailable"}},
            {"result": {"isError": True}},
            {"result": {"content": []}},
            {"result": None},
        ):
            with self.subTest(result=result):
                with self.assertRaises(RuntimeError):
                    self.run_probe(Gateway(result=result))

    def test_cleanup_failure_fails_check(self):
        with self.assertRaisesRegex(RuntimeError, "cleanup returned HTTP 500"):
            self.run_probe(Gateway(cleanup=500))

    def test_bearer_header_from_environment(self):
        with patch.dict(os.environ, {"MCP_API_KEY": "fixture-key"}, clear=True):
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
        self.assertEqual(loaded.BASE_HEADERS["Authorization"], "Bearer fixture-key")
        self.assertNotIn("X-API-Key", loaded.BASE_HEADERS)

    def test_missing_key_fails_before_connecting(self):
        with patch.object(probe, "MCP_API_KEY", None), patch.object(probe, "new_connection") as connect:
            with self.assertRaisesRegex(RuntimeError, "MCP_API_KEY is required"):
                probe.main()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
