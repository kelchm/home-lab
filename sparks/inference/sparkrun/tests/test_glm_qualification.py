"""Qualification against disposable local HTTP servers; no Spark/model assets needed."""
import base64
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qualification import evidence as q
from qualification import fixtures as f
from qualification import transport as t
cli = importlib.import_module("qualification.__main__")


def event(data):
    return b"data: " + json.dumps(data, ensure_ascii=False).encode() + b"\r\n\r\n"


def reply(body, cached=None):
    session = next(s for s in f.fixtures()["sessions"] if s["marker"] in body["messages"][0]["content"])
    if isinstance(body["tool_choice"], dict):
        delta = {"role": "assistant", "reasoning_content": "Reasoning café: use ledger factors.",
                 "tool_calls": [{"index": 0, "id": "call-" + session["marker"], "type": "function",
                                 "function": {"name": "multiply", "arguments": json.dumps({"a": session["a"], "b": session["b"]})}}]}
        finish = "tool_calls"
    else:
        text = session["answer"]
        if "confirmed" in body["messages"][-1]["content"]:
            text = "confirmed " + text
        delta = {"content": text, "reasoning_content": "Use only this session's result."}
        finish = "stop"
    usage = {"prompt_tokens": 200, "completion_tokens": 12, "total_tokens": 212}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return (b": keepalive\r\n\r\n" + event({"choices": [{"index": 0, "delta": delta}]})
            + event({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
            + event({"choices": [], "usage": usage}) + b"data: [DONE]\r\n\r\n")


@contextmanager
def server(mode="normal", mutate=lambda raw: raw, chunked=False, cached=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with self.server.lock:
                self.server.requests.append(payload)
            if mode == "headers":
                self.server.release.wait(1)
            self.send_response(500 if mode == "http-error" else 200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                if mode == "trickle":
                    while not self.server.release.wait(0.02):
                        self.wfile.write(b": still working\n\n")
                        self.wfile.flush()
                    return
                if mode in ("partial-line", "chunk-header", "chunk-body"):
                    self.wfile.write({"partial-line": b'data: {"choices":',
                                      "chunk-header": b"f", "chunk-body": b"100\r\ndata:"}[mode])
                    self.wfile.flush()
                    self.server.release.wait(1)
                    return
                # Ensure the first four intervals overlap even on a very fast loopback server.
                self.server.release.wait(0.015)
                raw = mutate(reply(payload, cached))
                if chunked:
                    # Split UTF-8, JSON and CRLF across HTTP chunks.
                    for byte in raw:
                        self.wfile.write(b"1\r\n" + bytes([byte]) + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(raw)
                self.wfile.flush()
                if mode == "keep-open":
                    self.server.release.wait(1)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    srv.release = threading.Event()
    srv.lock = threading.Lock()
    srv.requests = []
    srv.url = f"http://127.0.0.1:{srv.server_port}/v1"
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield srv
    finally:
        srv.release.set()
        srv.shutdown()
        srv.server_close()
        thread.join(2)


def payload():
    return f.body("fake", 17, f.fixtures()["sessions"][0]["messages"], 0)


def replace_raw(row, raw):
    row["response_b64"] = base64.b64encode(raw).decode()
    row["response_sha256"] = f.sha256(raw)
    try:
        row["receipt"] = t.parse(raw)
    except (ValueError, TypeError):
        row["receipt"] = None


class Fixtures(unittest.TestCase):
    def test_seed_hashes_contract_and_image_bytes(self):
        a, b = f.fixtures(), f.fixtures()
        self.assertEqual(f.digest(a), f.digest(b))
        self.assertEqual(f.digest(a), "70ed54fe000d5b35bb37e3cf1ea3a84583d44d19a36239e4afe6e5690f47e9b5")
        self.assertNotEqual(f.digest(a), f.digest(f.fixtures(42)))
        self.assertEqual(len(a["sessions"]), 4)
        self.assertEqual(len(a["images"]), 50)
        self.assertEqual(len({i["sha256"] for i in a["images"]}), 50)
        self.assertEqual(sum(i["token_cap"] for i in a["images"]), 102400)
        for entry in a["images"]:
            png, color = f.image(a["seed"], entry["index"])
            self.assertEqual(f.sha256(png), entry["sha256"])
            self.assertEqual(color, entry["expected_color"])
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            length = struct.unpack("!I", png[33:37])[0]
            self.assertEqual(len(zlib.decompress(png[41:41 + length])), 32 * 97)
        self.assertEqual(a["contract"]["total_window_tokens"], 1048576)
        self.assertEqual(a["contract"]["output_budget_tokens"], 32768)
        self.assertEqual(a["contract"]["aggregate_live_pool_floor_tokens"], 2582528)
        self.assertEqual(a["contract"]["max_videos"], 4)
        self.assertEqual(a["tool_sha256"], f.digest(f.TOOL))
        for session in a["sessions"]:
            self.assertEqual(session["text_sha256"], f.digest(session["messages"]))
            self.assertEqual(session["tool_input_sha256"], f.digest({"name": "multiply", "arguments": {
                "a": session["a"], "b": session["b"]}}))
            self.assertEqual(session["tool_result_sha256"], f.sha256(str(session["a"] * session["b"]).encode()))

    def test_fixture_export_hashes_match_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixtures"
            result = f.write_fixtures(target, 20261106)
            manifest = json.loads((target / "fixtures.json").read_text())
            self.assertEqual(result["fixture_sha256"], f.digest(manifest))
            self.assertEqual(len(list(target.glob("image-*.png"))), 50)
            for entry in manifest["images"]:
                self.assertEqual(entry["sha256"], f.sha256((target / entry["file"]).read_bytes()))
            with self.assertRaises(FileExistsError):
                f.write_fixtures(target, 20261106)


class Transport(unittest.TestCase):
    def test_fragmented_chunked_sse_usage_tools_and_unicode(self):
        with server(chunked=True) as srv:
            row = t.request(srv.url, payload(), 2)
        self.assertIsNone(row["error"])
        receipt = t.parse(base64.b64decode(row["response_b64"]))
        self.assertTrue(receipt["done"])
        self.assertEqual(receipt["finish_reason"], "tool_calls")
        self.assertIn("café", receipt["message"]["reasoning_content"])
        self.assertIsNone(receipt["cached_tokens"])

    def test_fragmented_tool_arguments_and_multiline_sse(self):
        raw = (b'data: {"choices":\n' + b'data: [{"index":0,"delta":{"tool_calls":[{"index":0,"id":"abc",'
               b'"function":{"name":"multi","arguments":"{\\"a\\":"}}]}}]}\n\n'
               + event({"choices": [{"index": 0, "delta": {"tool_calls": [
                   {"index": 0, "function": {"name": "ply", "arguments": "17}"}}]}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 3}})
               + b"data: [DONE]\n\n")
        stream = t.Stream()
        for byte in raw:
            stream.feed(bytes([byte]))
        receipt = stream.receipt()
        self.assertEqual(receipt["message"]["tool_calls"][0]["function"], {"name": "multiply", "arguments": '{"a":17}'})

    def test_done_does_not_wait_for_connection_close(self):
        with server(mode="keep-open") as srv:
            started = time.monotonic()
            row = t.request(srv.url, payload(), 0.7)
            self.assertLess(time.monotonic() - started, 0.6)
            self.assertIsNone(row["error"])

    def test_total_deadline_headers_trickle_partial_and_http_chunks(self):
        for mode in ("headers", "trickle", "partial-line", "chunk-header", "chunk-body"):
            with self.subTest(mode=mode), server(mode, chunked=mode.startswith("chunk")) as srv:
                started = time.monotonic()
                row = t.request(srv.url, payload(), 0.12)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(row["deadline_exceeded"], row)
                self.assertIn("DeadlineExceeded", row["error"])
                self.assertEqual(len(srv.requests), 1, "Requests must never retry")

    def test_dns_and_connect_wait_are_bounded(self):
        release = threading.Event()
        def blocked_connect(connection):
            release.wait(1)
            raise OSError("synthetic resolver stall")
        with mock.patch.object(t.http.client.HTTPConnection, "connect", blocked_connect):
            started = time.monotonic()
            row = t.request("http://127.0.0.1:9", payload(), 0.05)
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertTrue(row["deadline_exceeded"])
            release.set()

    def test_failed_and_malformed_streams_never_pass(self):
        mutations = {
            "no-done": lambda raw: raw.replace(b"data: [DONE]\r\n\r\n", b""),
            "no-finish": lambda raw: raw.replace(b'"finish_reason": "tool_calls"', b'"finish_reason": null'),
            "length": lambda raw: raw.replace(b'"finish_reason": "tool_calls"', b'"finish_reason": "length"'),
            "bad-usage": lambda raw: raw.replace(b'"completion_tokens": 12', b'"completion_tokens": true'),
            "bad-json": lambda raw: b"data: {bad}\n\n",
            "error": lambda raw: event({"error": {"message": "out of memory"}}),
            "empty": lambda raw: b"data: [DONE]\n\n",
            "no-usage": lambda raw: raw[:raw.index(b'data: {"choices": [], "usage"')] + b"data: [DONE]\n\n",
            "wrong-choice": lambda raw: raw.replace(b'"index": 0', b'"index": 1'),
            "duplicate-key": lambda raw: b'data: {"choices":[],"choices":[]}\n\n',
            "nonfinite": lambda raw: b'data: {"choices":[],"usage":NaN}\n\n',
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), server(mutate=mutate) as srv:
                row = t.request(srv.url, payload(), 1)
                self.assertIsNotNone(row["error"])
                self.assertTrue(row["response_b64"])
        with server("http-error") as srv:
            self.assertIn("HTTP 200", t.request(srv.url, payload(), 1)["error"])


class Evidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        args = SimpleNamespace(endpoint=None, model="fake", fixture_seed=20261106, request_seed=17,
                               arm="baseline", pair_id="pair-1", profile=None, source=[],
                               max_wall_regression=0.1, request_deadline=2, api_key_env=None,
                               output=str(cls.directory / "baseline.jsonl"))
        with server(cached=150) as srv:
            args.endpoint = srv.url
            cls.result = cli.run(args)
            cls.sent = srv.requests
        cls.rows = cli.load(args.output)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_bounded_run_preserves_reasoning_and_records_unknowns(self):
        self.assertEqual(self.result["workload_status"], "pass", self.result)
        self.assertEqual(self.result["status"], "unknown")
        self.assertEqual(len(self.sent), 12)
        self.assertEqual(self.rows[0]["profile_sha256"], None)
        self.assertEqual(self.rows[0]["source_sha256"], None)
        self.assertEqual(self.rows[0]["payload_token_count"], None)
        self.assertEqual(self.result["metrics"]["server_cached_tokens"], 1800)
        for body in self.sent:
            self.assertEqual(body["max_tokens"], 1024)
            self.assertTrue(body["chat_template_kwargs"]["enable_thinking"])
            for message in body["messages"]:
                if message["role"] == "assistant":
                    self.assertTrue(message["reasoning_content"])
        self.assertEqual(self.result["capabilities"]["cache_accounting"], "pass")
        self.assertTrue(all(value == "unknown" for key, value in self.result["capabilities"].items() if key != "cache_accounting"))

    def test_claimed_status_missing_rows_hashes_and_history_cannot_pass(self):
        for mutation in ("missing", "duplicate", "hash", "history", "usage", "malformed", "schema", "timing", "cache"):
            rows = copy.deepcopy(self.rows)
            request = next(r for r in rows if r["kind"] == "request" and r["turn"] == 1)
            if mutation == "missing":
                rows.remove(request)
            elif mutation == "duplicate":
                rows.append(copy.deepcopy(request))
            elif mutation == "hash":
                request["response_sha256"] = "0" * 64
            elif mutation == "history":
                request["body"]["messages"][2].pop("reasoning_content")
                request["request_sha256"] = f.digest(request["body"])
            elif mutation == "usage":
                raw = base64.b64decode(request["response_b64"]).replace(b'"prompt_tokens": 200', b'"prompt_tokens": 0')
                replace_raw(request, raw)
            elif mutation == "malformed":
                request["response_b64"] = "not base64"
            elif mutation == "schema":
                request["schema_version"] = "glm-qualification/v99"
            elif mutation == "timing":
                request["elapsed_s"] = 300
            elif mutation == "cache":
                next(r for r in rows if r["kind"] == "cache")["mode"] = "durable_restore"
            for row in rows:
                row["status"] = "pass"
            with self.subTest(mutation=mutation):
                self.assertEqual(q.validate(rows)["status"], "fail")

    def test_incorrect_answer_with_valid_wire_hash_and_usage_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r["kind"] == "request" and r["turn"] == 2)
        raw = base64.b64decode(row["response_b64"]).replace(b"confirmed agent-", b"confirmed wrong-")
        replace_raw(row, raw)
        self.assertEqual(q.validate(rows)["status"], "fail")

    def test_missing_cached_counts_stay_explicitly_unknown(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if row["kind"] == "request":
                raw = base64.b64decode(row["response_b64"]).replace(
                    b', "prompt_tokens_details": {"cached_tokens": 150}', b"")
                replace_raw(row, raw)
                row["server_usage"] = row["receipt"]["usage"]
                row["server_cached_tokens"] = None
            if row["kind"] == "cache":
                row["cached_tokens"] = None
        result = q.validate(rows)
        self.assertEqual(result["workload_status"], "pass", result)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["metrics"]["server_cached_tokens"])
        self.assertEqual(result["capabilities"]["cache_accounting"], "unknown")

    def external(self, kind, **extra):
        artifact = self.directory / "observation.log"
        artifact.write_text("synthetic instrumentation evidence\n")
        return {"schema_version": q.SCHEMA, "kind": kind, "run_id": self.rows[0]["run_id"],
                "profile_sha256": "a" * 64, "source_sha256": f.digest({"lock.json": "b" * 64}),
                "provenance": {"artifact_path": str(artifact), "artifact_sha256": f.sha256(artifact.read_bytes()),
                               "observer": "local-test"}, **extra}

    def complete_evidence(self):
        rows = copy.deepcopy(self.rows)
        rows[0].update(profile_sha256="a" * 64, source_sha256=f.digest({"lock.json": "b" * 64}),
                       source_artifacts={"lock.json": "b" * 64}, cache_condition="warm_prefix",
                       image_config_id="sha256:" + "9" * 64)
        values = {k: v for k, v in f.CONTRACT.items()
                  if k not in ("aggregate_live_pool_floor_tokens", "durable_gib_per_rank_floor")}
        rows.append(self.external("contract", values={**values, "durable_gib_per_rank": [64, 64]}))
        rows.append(self.external("capacity", values={"aggregate_live_pool_tokens": 2582528,
                         "observed_total_window_tokens": 1048576, "observed_output_tokens": 32768,
                         "concurrent_streams": 4, "correct": True}))
        rows.append(self.external("media", fixture_sha256=rows[0]["fixture_sha256"], values={
            "image_sha256": [i["sha256"] for i in f.fixtures()["images"]], "per_image_tokens": [2040] * 50,
            "per_image_token_cap": 2048,
            "history_tokens": {str(n): [2040] * n for n in (1, 2, 3, 49, 50)},
            "history_grid_sha256": {str(n): [f.sha256(f"grid-{i}".encode()) for i in range(n)]
                                    for n in (1, 2, 3, 49, 50)},
            "request_image_tokens": 102400, "video_clips": 4, "images_correct": True,
            "videos_correct": True, "video_fixture_sha256": "c" * 64}))
        rows.append(self.external("reasoning", values={"original_call_ids": ["id-a", "id-b"],
            "restored_call_ids": ["id-a", "id-b"], "original_reasoning_sha256": "d" * 64,
            "restored_reasoning_sha256": "d" * 64, "omitted_reasoning": True,
            "renumbered_id_cache_hit": False, "cross_session_cache_hit": False, "diagnostics_observed": True}))
        first = sorted((r for r in rows if r["kind"] == "request" and r["turn"] == 0), key=lambda r: r["session"])
        rows.append(self.external("cache_state", values={"condition": "warm_prefix",
            "request_ids": [r["request_id"] for r in first], "cached_tokens": [150] * 4}))
        rows.append(self.external("runtime", values={"observed_image_config_id": rows[0]["image_config_id"],
            "observed_source_sha256": rows[0]["source_sha256"], "instance_ids": ["rank-0", "rank-1"]}))
        for event in ("eviction_restore", "restart_restore", "crash_restore"):
            rows.append(self.external("durability", event=event, values={"storage": "nvme", "ranks": [0, 1],
                "before_instance_id": "boot-a", "after_instance_id": "boot-b", "prefix_sha256": "e" * 64,
                "before_cache_fingerprint_sha256": "f" * 64, "after_cache_fingerprint_sha256": "f" * 64,
                "restored_tokens": 1000, "disk_read_bytes": 65536, "correct": True}))
        return rows

    def test_full_contract_requires_affirmative_observations(self):
        rows = self.complete_evidence()
        self.assertEqual(q.validate(rows)["status"], "pass", q.validate(rows))
        for kind, key, value in (("contract", "max_images", 49), ("contract", "max_videos", 3),
                                 ("contract", "durable_gib_per_rank", [64, 63]),
                                 ("capacity", "aggregate_live_pool_tokens", 2582527),
                                 ("capacity", "observed_total_window_tokens", 1048575),
                                 ("capacity", "observed_output_tokens", 32767),
                                 ("media", "per_image_tokens", [4096] * 50),
                                 ("media", "per_image_token_cap", 1024),
                                 ("media", "history_tokens", {str(n): [1020] * n for n in (1, 2, 3, 49, 50)}),
                                 ("runtime", "observed_image_config_id", "sha256:" + "8" * 64),
                                 ("reasoning", "renumbered_id_cache_hit", True),
                                 ("reasoning", "diagnostics_observed", None),
                                 ("durability", "disk_read_bytes", 0),
                                 ("durability", "after_instance_id", "boot-a")):
            altered = copy.deepcopy(rows)
            target = next(r for r in altered if r["kind"] == kind and
                          (kind != "durability" or r["event"] == "restart_restore"))
            target["values"][key] = value
            with self.subTest(kind=kind, key=key):
                self.assertEqual(q.validate(altered)["status"], "fail")

    def test_missing_artifact_or_cold_prefill_cannot_prove_durability(self):
        rows = self.complete_evidence()
        target = next(r for r in rows if r["kind"] == "durability")
        target["provenance"]["artifact_sha256"] = "0" * 64
        self.assertEqual(q.validate(rows)["status"], "unknown")
        target["event"] = "cold_prefill"
        self.assertEqual(q.validate(rows)["status"], "fail")

    def candidate(self, rows, factor=1):
        result = copy.deepcopy(rows)
        result[0]["arm"] = "candidate"
        for row in result:
            row["run_id"] = "candidate-run"
            if row["kind"] == "request":
                for key in ("start_s", "end_s", "elapsed_s"):
                    row[key] *= factor
            if row["kind"] == "run_end":
                row["wall_s"] *= factor
        return result

    def test_compare_predeclared_tolerance_and_unknowns(self):
        baseline = self.complete_evidence()
        candidate = self.candidate(baseline, 1.05)
        self.assertEqual(q.compare(baseline, candidate)["status"], "unknown")
        self.assertEqual(q.compare(baseline, candidate)["performance_status"], "pass")
        self.assertEqual(q.compare(baseline, candidate)["improvement_status"], "unknown")
        self.assertEqual(q.compare(baseline, candidate)["adoption_status"], "unknown")
        self.assertFalse(q.compare(baseline, candidate)["token_equivalence_assumed"])
        slow = self.candidate(baseline, 1.20)
        self.assertEqual(q.compare(baseline, slow)["performance_status"], "fail")
        self.assertEqual(q.compare(self.rows, self.candidate(self.rows))["status"], "unknown")
        candidate[0]["policy"]["max_wall_regression_fraction"] = 0.2
        self.assertEqual(q.compare(baseline, candidate)["status"], "fail")

    def test_one_faster_pair_does_not_establish_improvement_or_adoption(self):
        baseline = self.complete_evidence()
        result = q.compare(baseline, self.candidate(baseline, 0.8))
        self.assertGreater(result["observed_wall_improvement_fraction"], 0)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["improvement_status"], "unknown")

    def test_unmatched_fixtures_self_comparison_and_swapped_arms_fail(self):
        candidate = self.candidate(self.rows)
        candidate[0]["pair_id"] = "different-pair"
        self.assertTrue(q.compare(self.rows, candidate)["errors"])
        self.assertEqual(q.compare(self.rows, self.rows)["status"], "fail")
        self.assertEqual(q.compare(candidate, self.rows)["status"], "fail")

    def test_compare_does_not_require_equal_engine_seeds_models_or_token_counts(self):
        baseline = self.complete_evidence()
        candidate = self.candidate(baseline)
        candidate[0].update(model="other-engine", request_seed=999)
        for row in candidate:
            if row["kind"] != "request":
                continue
            row["body"].update(model="other-engine", seed=999)
            row["request_sha256"] = f.digest(row["body"])
            raw = base64.b64decode(row["response_b64"])
            raw = raw.replace(b'"prompt_tokens": 200', b'"prompt_tokens": 300')
            raw = raw.replace(b'"completion_tokens": 12', b'"completion_tokens": 18')
            raw = raw.replace(b'"total_tokens": 212', b'"total_tokens": 318')
            replace_raw(row, raw)
            row["server_usage"] = row["receipt"]["usage"]
        self.assertEqual(q.compare(baseline, candidate)["status"], "unknown", q.compare(baseline, candidate))
        self.assertEqual(q.compare(baseline, candidate)["performance_status"], "pass")

    def test_cli_offline_never_calls_network_and_bad_json_is_failure(self):
        malformed = self.directory / "malformed.jsonl"
        malformed.write_text('{"status": "pass"}\nnot json\n')
        with mock.patch.object(cli, "request", side_effect=AssertionError("offline command used network")), \
                mock.patch("builtins.print"):
            self.assertEqual(cli.main(["validate", str(self.directory / "baseline.jsonl")]), 2)
            self.assertEqual(cli.main(["validate", str(malformed)]), 1)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            cli.main(["run", "--model", "fake", "--arm", "candidate", "--output", "unused", "--pair-id", "p"])

    def test_run_failure_retains_receipts_and_never_retries(self):
        args = SimpleNamespace(endpoint=None, model="fake", fixture_seed=20261106, request_seed=17,
                               arm="candidate", pair_id="fail", profile=None, source=[],
                               max_wall_regression=0.1, request_deadline=0.1, api_key_env=None,
                               output=str(self.directory / "failed.jsonl"))
        with server("partial-line") as srv:
            args.endpoint = srv.url
            result = cli.run(args)
            self.assertEqual(len(srv.requests), 4)
        self.assertEqual(result["status"], "fail")
        rows = cli.load(args.output)
        self.assertEqual(sum(r["kind"] == "request" for r in rows), 4)
        self.assertTrue(all(r["deadline_exceeded"] for r in rows if r["kind"] == "request"))
        self.assertEqual(rows[-1]["completed_tasks"], 0)


if __name__ == "__main__":
    unittest.main()
