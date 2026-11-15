"""Offline API fixtures exercise qualification failures, including optimized Python."""
import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("agent_acceptance.py")
spec = importlib.util.spec_from_file_location("agent_acceptance", SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        state = self.server.state
        if state["mode"] == "missing-metrics":
            self.wfile.write(b"# no gauges\n")
            return
        running = state["running"]
        text = f"tensorfold:requests_running {running}\ntensorfold:requests_waiting 0\n"
        if state["mode"] == "ambiguous-metrics":
            text += "tensorfold:requests_running{rank=\"1\"} 0\n"
        self.wfile.write(text.encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mode = self.server.state["mode"]
        self.send_response(200)
        self.end_headers()
        if "3000 words" in body["messages"][0]["content"]:
            self.server.state["running"] = 1
            try:
                # Remain live long enough that natural completion cannot prove cancellation.
                for _ in range(200):
                    data = {"id": "cancel-fixture", "choices": [{"delta": {"content": "index " * 100}}]}
                    self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if mode != "still-running":
                    self.server.state["running"] = 0
            return
        content = "323" if "Reply" in body["messages"][-1]["content"] else json.dumps({"answer": 323, "ok": True})
        if mode == "wrong-answer":
            content = '{"answer": 0, "ok": false}'
        finish = "error" if mode == "error-finish" else "length" if mode == "length-finish" else "stop"
        message = {"content": content}
        delta = message.copy()
        if isinstance(body.get("tool_choice"), dict):
            call = {"id": "multiply-fixture", "type": "function",
                    "function": {"name": "multiply", "arguments": '{"a":17,"b":19}'}}
            message = {"content": None, "tool_calls": [call]}
            delta = {"tool_calls": [{"index": 0, **call}]}
            finish = "stop" if mode == "tool-stop" else "error" if mode == "tool-error" else "tool_calls"
        engine = {"token_ids": [1] * 16} if body.get("return_token_ids") else None
        usage = {"prompt_tokens": 1, "completion_tokens": 16 if engine else 1}
        if not body.get("stream"):
            self.wfile.write(json.dumps({"choices": [{"message": message, "finish_reason": finish}],
                                        "usage": usage, "tensorfold": engine}).encode())
            return
        chunks = [{"choices": [{"delta": delta, "finish_reason":
                   "error" if mode == "stream-error" else finish}], "usage": usage, "tensorfold": engine}]
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        if mode != "missing-done":
            self.wfile.write(b"data: [DONE]\n\n")


class AgentAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "result.json"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server.daemon_threads = True
        self.server.state = {"mode": "good", "running": 0}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_cli(self, mode, section="structured", optimized=False):
        self.server.state.update(mode=mode, running=0)
        command = [sys.executable] + (["-O"] if optimized else []) + [str(SCRIPT),
            "--model", "fixture", "--url", f"http://127.0.0.1:{self.server.server_port}/v1",
            "--output", str(self.output), "--section", section, "--cancel-timeout", ".3"]
        return subprocess.run(command, text=True, capture_output=True, timeout=10)

    def test_valid_stream_and_nonstream_pass_in_both_python_modes(self):
        for optimized in (False, True):
            result = self.run_cli("good", optimized=optimized)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(self.output.read_text())), 2)

    def test_bad_finishes_missing_done_and_wrong_answer_fail_even_with_optimization(self):
        for optimized in (False, True):
            for mode, diagnostic in (("error-finish", "Unexpected finish_reason"),
                                     ("length-finish", "Unexpected finish_reason"),
                                     ("stream-error", "Unexpected stream finish_reason"),
                                     ("missing-done", "Incomplete stream"),
                                     ("wrong-answer", "Qualification failed")):
                with self.subTest(optimized=optimized, mode=mode):
                    result = self.run_cli(mode, optimized=optimized)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(diagnostic, result.stderr)

    def test_named_tool_stop_remains_explicitly_allowed_but_error_does_not(self):
        result = self.run_cli("good", "tools", optimized=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(self.output.read_text())), 5)
        result = self.run_cli("tool-stop", "tools", optimized=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LIMITATION", result.stdout)
        result = self.run_cli("tool-error", "tools", optimized=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected finish_reason", result.stderr)

    def test_live_cut_call_probe_accepts_its_deliberate_output_limit(self):
        self.server.state["mode"] = "length-finish"
        tokenizer = SimpleNamespace(from_file=lambda _: SimpleNamespace(
            decode=lambda tokens, **kwargs: "<tool_call>incomplete"))
        spec = importlib.util.spec_from_file_location("cut_probe_fixture", SCRIPT.with_name("mia_tensorfold_cut_calls.py"))
        cut = importlib.util.module_from_spec(spec)
        argv = ["cut-probe", "--model", "fixture", "--url", f"http://127.0.0.1:{self.server.server_port}/v1",
                "--output", str(self.output), "--tokenizer", "unused-fixture"]
        with patch.dict(sys.modules, {"tokenizers": SimpleNamespace(Tokenizer=tokenizer), "agent_acceptance": agent}):
            spec.loader.exec_module(cut)
        with patch.object(sys, "argv", argv):
            cut.main()
        rows = json.loads(self.output.read_text())
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"pass"})
        self.assertEqual({row["result"]["choices"][0]["finish_reason"] for row in rows}, {"length"})

    def test_cancellation_requires_observed_stop_and_records_evidence(self):
        result = self.run_cli("good", "cancellation", optimized=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(self.output.read_text())[0]["result"]["cancellation_evidence"]
        self.assertEqual(evidence["before"], [0, 0])
        self.assertEqual(evidence["during"], [1, 0])
        self.assertEqual(evidence["after_disconnect"][-2:], [[0, 0], [0, 0]])
        self.assertEqual(evidence["request_id"], "cancel-fixture")

    def test_fast_continuation_cannot_hide_request_that_kept_running(self):
        for optimized in (False, True):
            result = self.run_cli("still-running", "cancellation", optimized)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cancelled request still running", result.stderr)
            self.assertFalse(self.output.exists())

    def test_missing_or_ambiguous_cancellation_evidence_fails(self):
        for mode in ("missing-metrics", "ambiguous-metrics"):
            result = self.run_cli(mode, "cancellation", optimized=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing or ambiguous cancellation metric", result.stderr)

    def test_generated_code_wrong_result_and_mutation_fail_explicitly(self):
        real_run = subprocess.run

        def run_child(command, **kwargs):
            # macOS cannot set this Linux qualification's RLIMIT_AS. Keep these
            # tests about actual generated-code gates, rather than that OS failure.
            if sys.platform == "darwin":
                command = [*command[:-1], "\n".join(line for line in command[-1].splitlines()
                           if not line.startswith("resource.setrlimit"))]
            return real_run(command, **kwargs)

        with patch.object(agent.subprocess, "run", side_effect=run_child):
            for code, diagnostic in (("def f(x): return 0", "0, 2"),
                                     ("def f(x):\n x.append(1)\n return 2", "input mutated")):
                with self.assertRaisesRegex(RuntimeError, diagnostic):
                    agent.execute(code, "f", [[[]]], [2])
            agent.execute("def f(x): return 2", "f", [[[]]], [2])


if __name__ == "__main__":
    unittest.main()
