"""Exercise the optional benchmark client against disposable loopback SSE sockets."""

from contextlib import contextmanager
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock


BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks/long_sessions.py"
MARKER = "synthetic-marker"
PARTIAL = {"choices": [{"delta": {"content": MARKER + " café"}}]}
FINISH = {"choices": [{"finish_reason": "stop"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 3}, "tensorfold": {"synthetic": True}}


class SyntheticTokenizer:
    @classmethod
    def from_file(cls, path):
        return cls()

    def encode(self, text):
        return SimpleNamespace(ids=list(text.encode()))


def load_benchmark():
    spec = importlib.util.spec_from_file_location("long_sessions", BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    # Only token counting is synthetic: no tokenizer package or model asset is needed.
    with mock.patch.dict(sys.modules, {"tokenizers": SimpleNamespace(Tokenizer=SyntheticTokenizer)}):
        spec.loader.exec_module(module)
    return module


def event(data):
    return b"data: " + json.dumps(data, ensure_ascii=False).encode() + b"\r\n\r\n"


@contextmanager
def sse_server(parts, *, chunked=False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.server.request_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            for part in parts:
                self.wfile.write(part)
                self.wfile.flush()
            self.server.sent.set()
            # Keep the connection silent and open until the client closes or test cleanup runs.
            self.connection.settimeout(0.05)
            while not self.server.release.is_set():
                try:
                    if not self.connection.recv(1):
                        self.server.disconnected.set()
                        return
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    self.server.disconnected.set()
                    return

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.sent = threading.Event()
    server.release = threading.Event()
    server.disconnected = threading.Event()
    server.url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive(), "SSE server did not stop"


@contextmanager
def request_worker(campaign, server, **kwargs):
    result = SimpleNamespace(row=None, error=None)

    def run():
        try:
            result.row = campaign.request("synthetic prompt", case="local-sse", **kwargs)
        except Exception as error:
            result.error = error

    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield thread, result
    finally:
        # Also bounds the negative control: release the old blocking reader after assertion failure.
        server.release.set()
        thread.join(3)
        assert not thread.is_alive(), "request worker did not stop after server cleanup"


class LongSessionStreams(unittest.TestCase):
    def setUp(self):
        self.benchmark = load_benchmark()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def campaign(self, url, timeout=2400):
        return self.benchmark.Campaign(SimpleNamespace(
            url=url, timeout=timeout, tokenizer="synthetic", model="synthetic", seed=17,
            marker=MARKER, arm="local-test", output=str(Path(self.directory.name) / "results.jsonl")))

    def receipt(self, campaign):
        rows = [json.loads(line) for line in campaign.failed_responses.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["case"], "local-sse")
        self.assertEqual(row["arm"], "local-test")
        self.assertEqual(row["request_seed"], 17)
        self.assertEqual(row["request_max_tokens"], 256)
        self.assertGreater(row["elapsed_s"], 0)
        return row

    def cancel_silent_read(self, prefix, tail, *, chunked=False, reason=True):
        parsed = threading.Event()

        def observe_json(payload):
            value = json.loads(payload)
            if value == PARTIAL:
                parsed.set()
            return value

        parts = [prefix, tail]
        with sse_server(parts, chunked=chunked) as server:
            campaign = self.campaign(server.url)
            # Observe real JSON parsing to synchronize cancellation after the content was read.
            with mock.patch.object(self.benchmark, "json", SimpleNamespace(dumps=json.dumps, loads=observe_json)):
                with request_worker(campaign, server) as (thread, result):
                    self.assertTrue(server.sent.wait(3), "server did not send headers/body prefix")
                    if prefix:
                        self.assertTrue(parsed.wait(3), "client did not parse the partial content")
                    thread.join(0.15)
                    self.assertTrue(thread.is_alive(), "silent server unexpectedly completed the request")
                    if reason:
                        campaign.failures.append("synthetic cancellation")
                    started = time.monotonic()
                    campaign.cancel.set()
                    thread.join(2)
                    elapsed = time.monotonic() - started
                    self.assertFalse(thread.is_alive(), "cancellation blocked on the silent SSE socket for >2s")
                    self.assertLess(elapsed, 2)
                    self.assertIsInstance(result.error, RuntimeError)
                    self.assertIn("Campaign aborted", str(result.error))
                    if reason:
                        self.assertIn("synthetic cancellation", str(result.error))
                    self.assertTrue(server.disconnected.wait(1), "response socket was not closed")
                    row = self.receipt(campaign)
                    self.assertEqual(row["text"], PARTIAL["choices"][0]["delta"]["content"] if prefix else "")
                    self.assertEqual(row["first"] is not None, bool(prefix))
                    self.assertIsNone(row["finish_reason"])
                    self.assertIsNone(row["usage"])
                    self.assertEqual(row["validation_error"], f"RuntimeError: {result.error}")
                    self.assertFalse(any(t.name == "long-session-cancel" for t in threading.enumerate()))
                    return {"cancellation_s": elapsed, "failure_receipt": row}

    def test_cancel_before_first_sse_line(self):
        self.cancel_silent_read(b"", b"")

    def test_cancel_with_no_monitor_failure(self):
        self.cancel_silent_read(b"", b"", reason=False)

    def test_cancel_in_partial_sse_line(self):
        self.cancel_silent_read(b"", b'data: {"choices":')

    def test_cancel_after_partial_response(self):
        self.cancel_silent_read(event(PARTIAL), b'data: {"choices":')

    def test_cancel_in_chunk_header_after_partial_response(self):
        part = event(PARTIAL)
        self.cancel_silent_read(f"{len(part):x}\r\n".encode() + part + b"\r\n", b"f", chunked=True)

    def test_cancel_in_chunk_body_before_first_line(self):
        self.cancel_silent_read(b"", b"100\r\ndata:", chunked=True)

    def test_normal_chunked_stream_preserves_outcome(self):
        data = b": heartbeat\r\n\r\n" + event(PARTIAL) + event(FINISH) + b"data: [DONE]\r\n\r\n"
        # One-byte HTTP chunks split JSON, CRLF, and UTF-8; the server stays open even after DONE.
        parts = [b"1\r\n" + bytes([byte]) + b"\r\n" for byte in data]
        with sse_server(parts, chunked=True) as server:
            campaign = self.campaign(server.url)
            with request_worker(campaign, server, expected=MARKER + " café", min_completion=3) as (thread, result):
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertIsNone(result.error)
                self.assertEqual(result.row["text"], MARKER + " café")
                self.assertEqual(result.row["usage"], FINISH["usage"])
                self.assertEqual(result.row["engine_stats"], FINISH["tensorfold"])
                self.assertEqual(result.row["finish_reason"], "stop")
                self.assertGreaterEqual(result.row["ttft_s"], 0)
                self.assertFalse(campaign.failed_responses.exists())
                self.assertTrue(server.disconnected.wait(1))
                self.assertEqual(server.request_body["stream_options"], {"include_usage": True})
                self.assertFalse(any(t.name == "long-session-cancel" for t in threading.enumerate()))

    def test_socket_timeout_preserves_partial_failure(self):
        with sse_server([event(PARTIAL)]) as server:
            campaign = self.campaign(server.url, timeout=0.3)
            with request_worker(campaign, server) as (thread, result):
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertIsInstance(result.error, TimeoutError)
                row = self.receipt(campaign)
                self.assertEqual(row["text"], MARKER + " café")
                self.assertIn("TimeoutError", row["validation_error"])
                self.assertFalse(campaign.cancel.is_set())
                self.assertTrue(server.disconnected.wait(1))
                self.assertFalse(any(t.name == "long-session-cancel" for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
