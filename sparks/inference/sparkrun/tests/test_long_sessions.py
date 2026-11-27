"""Exercise the optional benchmark client against disposable loopback SSE sockets."""

from contextlib import contextmanager
import importlib.util
from http.client import HTTPResponse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import shlex
import socket
import subprocess
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
FINISH = {"choices": [{"finish_reason": "stop"}]}
USAGE = {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3},
         "tensorfold": {"synthetic": True}}


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
            try:
                for part in parts:
                    self.wfile.write(part)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.server.disconnected.set()
                return
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

    def test_write_side_reset_marks_disconnected_and_returns(self):
        with sse_server([event(PARTIAL), event(FINISH)]) as server:
            for operation in ("write", "flush"):
                with self.subTest(operation=operation):
                    server.disconnected.clear()
                    writer = mock.Mock()
                    getattr(writer, operation).side_effect = ConnectionResetError("synthetic reset")
                    handler = SimpleNamespace(
                        server=server, rfile=BytesIO(b"{}"), headers={"Content-Length": "2"},
                        wfile=writer, send_response=mock.Mock(), send_header=mock.Mock(),
                        end_headers=mock.Mock())
                    # Invoke the real handler synchronously: falling through after the reset fails.
                    server.RequestHandlerClass.do_POST(handler)
                    self.assertTrue(server.disconnected.is_set())
                    self.assertFalse(server.sent.is_set())
                    self.assertEqual(writer.write.call_count, 1)
                    self.assertEqual(writer.flush.call_count, int(operation == "flush"))

    def test_normal_chunked_stream_preserves_outcome(self):
        done_read = threading.Event()
        readline = HTTPResponse.readline

        def observe_readline(response, *args):
            line = readline(response, *args)
            if line.strip() == b"data: [DONE]":
                done_read.set()
            return line

        data = b": heartbeat\r\n\r\n" + event(PARTIAL) + event(FINISH) + event(USAGE) + b"data: [DONE]\r\n\r\n"
        # One-byte HTTP chunks split JSON, CRLF, and UTF-8; the server stays open even after DONE.
        parts = [b"1\r\n" + bytes([byte]) + b"\r\n" for byte in data]
        with sse_server(parts, chunked=True) as server, mock.patch.object(HTTPResponse, "readline", observe_readline):
            campaign = self.campaign(server.url)
            with request_worker(campaign, server, expected=MARKER + " café", min_completion=3) as (thread, result):
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertIsNone(result.error)
                self.assertEqual(result.row["text"], MARKER + " café")
                self.assertEqual(result.row["usage"], USAGE["usage"])
                self.assertEqual(result.row["engine_stats"], USAGE["tensorfold"])
                self.assertEqual(result.row["finish_reason"], "stop")
                self.assertTrue(done_read.is_set(), "client did not read [DONE]")
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


def meminfo_text(available_gib, swap_total_gib=15.0, swap_free_gib=15.0):
    avail_kb = int(available_gib * 1024 ** 2)
    swap_total_kb = int(swap_total_gib * 1024 ** 2)
    swap_free_kb = int(swap_free_gib * 1024 ** 2)
    return (
        f"MemTotal:       128000000 kB\n"
        f"MemAvailable:   {avail_kb} kB\n"
        f"SwapTotal:      {swap_total_kb} kB\n"
        f"SwapFree:       {swap_free_kb} kB\n"
    )


class LongSessionMonitoring(unittest.TestCase):
    RECIPE = "/home/kelchm/sparkrun/recipes/glm53/glm53-test.yaml"
    WORKER = "kelchm@10.32.21.32"
    HEAD_ID = "a" * 64
    WORKER_ID = "b" * 64
    REPLACEMENT_HEAD_ID = "c" * 64
    REPLACEMENT_WORKER_ID = "d" * 64

    def setUp(self):
        self.benchmark = load_benchmark()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def campaign(self, *, require_stable_links=False, url="http://127.0.0.1:8888/v1", suite="ceiling"):
        return self.benchmark.Campaign(SimpleNamespace(
            url=url,
            timeout=30,
            tokenizer="synthetic",
            model="synthetic",
            seed=17,
            marker=MARKER,
            arm="local-test",
            recipe=self.RECIPE,
            worker=self.WORKER,
            suite=suite,
            campaign="monitor-test",
            require_stable_links=require_stable_links,
            output=str(Path(self.directory.name) / "results.jsonl"),
        ))

    def test_unidentified_or_ambiguous_rank_exits_before_sending_load(self):
        cases = [
            ("missing_head", "", self.WORKER_ID + "\n", "Could not identify rank 0"),
            ("missing_worker", self.HEAD_ID + "\n", "", "Could not identify rank 1"),
            ("ambiguous_head", f"{self.HEAD_ID}\n{self.REPLACEMENT_HEAD_ID}\n", self.WORKER_ID + "\n", "Ambiguous rank 0"),
            ("ambiguous_worker", self.HEAD_ID + "\n", f"{self.WORKER_ID}\n{self.REPLACEMENT_WORKER_ID}\n", "Ambiguous rank 1"),
            ("invalid_id", "not-a-hex-id\n", self.WORKER_ID + "\n", "Invalid rank 0 container ID"),
        ]
        for name, head_out, worker_out, expected_msg in cases:
            with self.subTest(case=name):
                campaign = self.campaign()

                def fake_check_output(cmd, **kwargs):
                    if cmd[0] == "docker":
                        return head_out
                    if cmd[0] == "ssh":
                        return worker_out
                    raise AssertionError(f"Unexpected command: {cmd}")

                with (
                    mock.patch.object(self.benchmark.subprocess, "check_output", side_effect=fake_check_output),
                    mock.patch.object(self.benchmark.subprocess, "run") as mock_run,
                    mock.patch.object(campaign, "phase") as mock_phase,
                    mock.patch.object(campaign, "request") as mock_request,
                ):
                    with self.assertRaisesRegex(RuntimeError, expected_msg):
                        campaign.run()
                    mock_phase.assert_not_called()
                    mock_request.assert_not_called()
                    mock_run.assert_not_called()
                    self.assertFalse(campaign.memory.is_alive())

    def test_lost_telemetry_cancels_requests_without_stopping_service(self):
        campaign = self.campaign()
        campaign.rank_containers = {("head", 0): self.HEAD_ID, (self.WORKER, 1): self.WORKER_ID}

        def fake_read_text(path_self, *args, **kwargs):
            if str(path_self) == "/proc/meminfo":
                return meminfo_text(10.0)
            raise AssertionError(f"Unexpected local read: {path_self}")

        def fake_check_output(cmd, **kwargs):
            raise subprocess.CalledProcessError(255, cmd, "ssh: connect timed out")

        with (
            mock.patch.object(Path, "read_text", fake_read_text),
            mock.patch.object(self.benchmark.subprocess, "check_output", side_effect=fake_check_output),
            mock.patch.object(self.benchmark.subprocess, "run") as mock_run,
        ):
            campaign.monitor()

        self.assertTrue(campaign.cancel.is_set())
        self.assertEqual(len(campaign.failures), 1)
        self.assertIn("Campaign monitoring failed", campaign.failures[0])
        mock_run.assert_not_called()

    def test_telemetry_parse_and_write_failures_leave_service_running(self):
        for mode in ("parse", "write"):
            with self.subTest(mode=mode):
                campaign = self.campaign()
                campaign.rank_containers = {("head", 0): self.HEAD_ID, (self.WORKER, 1): self.WORKER_ID}
                raw_head = "corrupted-meminfo-line" if mode == "parse" else meminfo_text(10.0)

                def fake_read_text(path_self, *args, **kwargs):
                    if str(path_self) == "/proc/meminfo":
                        return raw_head
                    raise AssertionError(f"Unexpected local read: {path_self}")

                real_open = Path.open

                def failing_open(path_self, *args, **kwargs):
                    if mode == "write" and path_self == campaign.telemetry:
                        raise OSError("No space left on device")
                    return real_open(path_self, *args, **kwargs)

                with (
                    mock.patch.object(Path, "read_text", fake_read_text),
                    mock.patch.object(Path, "open", failing_open),
                    mock.patch.object(self.benchmark.subprocess, "run") as mock_run,
                ):
                    campaign.monitor()

                self.assertTrue(campaign.cancel.is_set())
                self.assertEqual(len(campaign.failures), 1)
                self.assertIn("Campaign monitoring failed", campaign.failures[0])
                mock_run.assert_not_called()

    def test_failed_link_check_cancels_requests_without_stopping_service(self):
        campaign = self.campaign(require_stable_links=True)
        campaign.rank_containers = {("head", 0): self.HEAD_ID, (self.WORKER, 1): self.WORKER_ID}
        worker_drops = iter([0, 1])

        def fake_read_text(path_self, *args, **kwargs):
            mapping = {
                "/proc/meminfo": meminfo_text(10.0),
                "/sys/class/net/enP7s7/carrier": "1",
                "/sys/class/net/enP7s7/speed": "10000",
                "/sys/class/net/enP7s7/carrier_down_count": "0",
            }
            return mapping[str(path_self)]

        def fake_check_output(cmd, **kwargs):
            drops = next(worker_drops)
            return f"{meminfo_text(10.0).strip()}\n1\n10000\n{drops}\n"

        with (
            mock.patch.object(Path, "read_text", fake_read_text),
            mock.patch.object(self.benchmark.subprocess, "check_output", side_effect=fake_check_output),
            mock.patch.object(self.benchmark.subprocess, "run") as mock_run,
            mock.patch.object(campaign.finished, "wait", return_value=False),
        ):
            campaign.monitor()

        self.assertTrue(campaign.cancel.is_set())
        self.assertEqual(len(campaign.failures), 1)
        self.assertIn("management link changed", campaign.failures[0])
        mock_run.assert_not_called()

    def test_low_memory_stops_only_original_rank_ids_preserves_replacement_and_continues_on_stop_error(self):
        running_containers = {
            ("head", self.HEAD_ID): {"recipe": self.RECIPE, "rank": 0, "running": True},
            (self.WORKER, self.WORKER_ID): {"recipe": self.RECIPE, "rank": 1, "running": True},
        }
        campaign = self.campaign()
        stop_calls = []

        def fake_check_output(cmd, **kwargs):
            if cmd[0] == "docker" and cmd[1] == "ps":
                return self.HEAD_ID + "\n"
            if cmd[0] == "ssh" and "docker ps" in cmd[-1]:
                return self.WORKER_ID + "\n"
            if cmd[0] == "ssh" and "cat" in cmd:
                # Worker stays low (<2 GiB) for three consecutive samples.
                return meminfo_text(1.5)
            raise AssertionError(f"Unexpected check_output: {cmd}")

        def fake_read_text(path_self, *args, **kwargs):
            if str(path_self) == "/proc/meminfo":
                return meminfo_text(10.0)
            raise AssertionError(f"Unexpected local read: {path_self}")

        def fake_run(cmd, **kwargs):
            stop_calls.append(cmd)
            if cmd[0] == "sparkrun":
                raise AssertionError("Emergency teardown must never call sparkrun stop by recipe")
            if cmd[0] == "docker" and cmd[1] == "stop":
                cid = cmd[2]
                # Simulate head stop failure: worker stop must still execute.
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"cannot stop {cid}")
            if cmd[0] == "ssh":
                remote = shlex.split(cmd[-1])
                self.assertEqual(remote[:2], ["docker", "stop"])
                cid = remote[2]
                if (self.WORKER, cid) in running_containers:
                    running_containers[(self.WORKER, cid)]["running"] = False
                return subprocess.CompletedProcess(cmd, 0, stdout=cid + "\n", stderr="")
            raise AssertionError(f"Unexpected run: {cmd}")

        campaign.rank_containers = None
        with (
            mock.patch.object(Path, "read_text", fake_read_text),
            mock.patch.object(self.benchmark.subprocess, "check_output", side_effect=fake_check_output),
            mock.patch.object(self.benchmark.subprocess, "run", side_effect=fake_run),
            mock.patch.object(campaign.finished, "wait", return_value=False),
        ):
            campaign.rank_containers = campaign.identify_ranks()
            # Same-recipe replacement containers start after rank capture; they must survive emergency stop.
            running_containers[("head", self.REPLACEMENT_HEAD_ID)] = {"recipe": self.RECIPE, "rank": 0, "running": True}
            running_containers[(self.WORKER, self.REPLACEMENT_WORKER_ID)] = {"recipe": self.RECIPE, "rank": 1, "running": True}
            campaign.monitor()

        self.assertTrue(campaign.cancel.is_set())
        self.assertEqual(len(stop_calls), 2)
        self.assertEqual(stop_calls[0], ["docker", "stop", self.HEAD_ID])
        self.assertEqual(stop_calls[1][0], "ssh")
        self.assertEqual(shlex.split(stop_calls[1][-1]), ["docker", "stop", self.WORKER_ID])
        # Original worker rank was stopped despite head stop failure; both replacements survived.
        self.assertFalse(running_containers[(self.WORKER, self.WORKER_ID)]["running"])
        self.assertTrue(running_containers[("head", self.REPLACEMENT_HEAD_ID)]["running"])
        self.assertTrue(running_containers[(self.WORKER, self.REPLACEMENT_WORKER_ID)]["running"])
        # Both the low-memory cause and the head stop failure are reported.
        joined = "; ".join(campaign.failures)
        self.assertIn("MemAvailable below 2 GiB for three samples", joined)
        self.assertIn(f"Emergency stop failed for rank 0 ({self.HEAD_ID}) on head", joined)

    def test_low_memory_worker_stop_exception_still_reports_and_does_not_skip_head(self):
        campaign = self.campaign()
        campaign.rank_containers = {("head", 0): self.HEAD_ID, (self.WORKER, 1): self.WORKER_ID}
        stop_calls = []

        def fake_read_text(path_self, *args, **kwargs):
            return meminfo_text(1.2)

        def fake_check_output(cmd, **kwargs):
            return meminfo_text(10.0)

        def fake_run(cmd, **kwargs):
            stop_calls.append(cmd)
            if cmd[0] == "docker":
                return subprocess.CompletedProcess(cmd, 0, stdout=self.HEAD_ID, stderr="")
            raise subprocess.TimeoutExpired(cmd, 30)

        with (
            mock.patch.object(Path, "read_text", fake_read_text),
            mock.patch.object(self.benchmark.subprocess, "check_output", side_effect=fake_check_output),
            mock.patch.object(self.benchmark.subprocess, "run", side_effect=fake_run),
            mock.patch.object(campaign.finished, "wait", return_value=False),
        ):
            campaign.monitor()

        self.assertEqual(len(stop_calls), 2)
        self.assertEqual(stop_calls[0], ["docker", "stop", self.HEAD_ID])
        joined = "; ".join(campaign.failures)
        self.assertIn("head MemAvailable below 2 GiB for three samples", joined)
        self.assertIn(f"Emergency stop failed for rank 1 ({self.WORKER_ID}) on {self.WORKER}", joined)


if __name__ == "__main__":
    unittest.main()
