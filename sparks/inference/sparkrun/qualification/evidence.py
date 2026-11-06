"""Versioned JSONL receipts and fail-closed offline validation/comparison."""
import base64
import math
from pathlib import Path
import re

from .fixtures import CONTRACT, POLICY, VERSION, body, digest, fixtures, sha256
from .transport import parse, strict_json


SCHEMA = "glm-qualification/v1"
KINDS = {"run", "request", "cache", "cache_state", "run_end", "contract", "capacity", "media", "reasoning", "durability", "runtime"}


def is_hash(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def advance(session, turn, receipt, history):
    """Check task semantics and build the exact next preserved-thinking history."""
    message = receipt["message"]
    calls = message.get("tool_calls", [])
    if turn == 0:
        require(receipt["finish_reason"] == "tool_calls" and len(calls) == 1, "Expected one finished tool call")
        call = calls[0]
        require(isinstance(call["id"], str) and bool(call["id"].strip()), "Missing server tool call ID")
        require(call["function"]["name"] == "multiply", "Wrong tool name")
        arguments = strict_json(call["function"]["arguments"])
        require(arguments == {"a": session["a"], "b": session["b"]} and
                all(type(v) is int for v in arguments.values()), "Incorrect tool arguments")
        require(bool(message["reasoning_content"].strip()), "Missing thinking on tool turn")
        return history + [message, {"role": "tool", "tool_call_id": call["id"],
                                   "content": str(session["a"] * session["b"])},
                          {"role": "user", "content": "Reply with exactly marker:product, no extra text."}]
    expected = session["answer"] if turn == 1 else "confirmed " + session["answer"]
    require(receipt["finish_reason"] == "stop" and not calls, "Expected stop without tools")
    require(message["content"].strip() == expected, "Incorrect final answer or session isolation failure")
    return history + [message, {"role": "user", "content":
                               "Recall this session's marker and tool result. Reply with exactly confirmed marker:product."}]


def provenance(row, artifact_root):
    proof = row.get("provenance")
    if not isinstance(proof, dict) or not is_hash(proof.get("artifact_sha256")) or not proof.get("observer"):
        return False
    path = proof.get("artifact_path")
    if not isinstance(path, str) or not path:
        return False
    try:
        artifact = Path(path)
        if not artifact.is_absolute():
            artifact = Path(artifact_root) / artifact
        return sha256(artifact.read_bytes()) == proof["artifact_sha256"]
    except OSError:
        return False


def capability_checks(rows, run, artifact_root):
    """Normalized external observations are operator receipts, backed by retained artifacts.

    They cannot be derived from a short correct response, cached_tokens alone, or config.
    """
    checks = {}

    def check(kind, predicate, event=None):
        name = kind if event is None else f"{kind}:{event}"
        entries = [r for r in rows if r["kind"] == kind and (event is None or r.get("event") == event)]
        if not entries:
            checks[name] = "unknown"
            return
        require(len(entries) == 1, f"Duplicate {name} receipt")
        row = entries[0]
        require(row.get("profile_sha256") == run.get("profile_sha256") and is_hash(row.get("profile_sha256")),
                f"{name} not bound to this profile")
        require(row.get("source_sha256") == run.get("source_sha256") and is_hash(row.get("source_sha256")),
                f"{name} not bound to this source")
        if not provenance(row, artifact_root):
            checks[name] = "unknown"
        else:
            checks[name] = "pass" if predicate(row) else "fail"

    def contract(row):
        values = row.get("values", {})
        exact = {k: v for k, v in CONTRACT.items()
                 if k not in ("aggregate_live_pool_floor_tokens", "durable_gib_per_rank_floor")}
        return (all(type(values.get(k)) is int and values[k] == v for k, v in exact.items())
                and isinstance(values.get("durable_gib_per_rank"), list)
                and len(values["durable_gib_per_rank"]) == 2
                and all(number(v) and v >= 64 for v in values["durable_gib_per_rank"]))

    def capacity(row):
        values = row.get("values", {})
        return (all(type(values.get(k)) is int and values[k] >= v for k, v in {
            "aggregate_live_pool_tokens": 2582528, "observed_total_window_tokens": 1048576,
            "observed_output_tokens": 32768}.items())
                and values.get("concurrent_streams") == 4 and type(values.get("concurrent_streams")) is int
                and values.get("correct") is True)

    def media(row):
        values = row.get("values", {})
        bundle = fixtures(run["fixture_seed"])
        tokens = values.get("per_image_tokens")
        history = values.get("history_tokens", {})
        grids = values.get("history_grid_sha256", {})
        checkpoints = (1, 2, 3, 49, 50)
        return (row.get("fixture_sha256") == run["fixture_sha256"]
                and values.get("image_sha256") == [i["sha256"] for i in bundle["images"]]
                and values.get("per_image_token_cap") == 2048
                and type(values.get("per_image_token_cap")) is int
                and isinstance(tokens, list) and len(tokens) == 50
                and all(type(v) is int and 0 < v <= 2048 for v in tokens)
                and isinstance(history, dict) and isinstance(grids, dict)
                and set(history) == set(grids) == {str(n) for n in checkpoints}
                and isinstance(grids["50"], list) and len(grids["50"]) == 50
                and all(is_hash(v) for v in grids["50"])
                and all(history[str(n)] == tokens[:n] and grids[str(n)] == grids["50"][:n]
                        for n in checkpoints)
                and values.get("request_image_tokens") == 102400
                and type(values.get("request_image_tokens")) is int
                and values.get("video_clips") == 4 and type(values.get("video_clips")) is int
                and values.get("images_correct") is True and values.get("videos_correct") is True
                and is_hash(values.get("video_fixture_sha256")))

    def reasoning(row):
        values = row.get("values", {})
        ids = values.get("original_call_ids")
        return (isinstance(ids, list) and len(ids) >= 2 and len(set(ids)) == len(ids)
                and all(isinstance(v, str) and bool(v) for v in ids)
                and values.get("restored_call_ids") == ids
                and is_hash(values.get("original_reasoning_sha256"))
                and values.get("restored_reasoning_sha256") == values["original_reasoning_sha256"]
                and values.get("omitted_reasoning") is True
                and values.get("renumbered_id_cache_hit") is False
                and values.get("cross_session_cache_hit") is False
                and values.get("diagnostics_observed") is True)

    def durable(row):
        values = row.get("values", {})
        event = row["event"]
        before, after = values.get("before_instance_id"), values.get("after_instance_id")
        return (values.get("storage") == "nvme" and values.get("ranks") == [0, 1]
                and all(type(v) is int for v in values["ranks"])
                and isinstance(before, str) and bool(before) and isinstance(after, str) and bool(after)
                and (event == "eviction_restore" or before != after)
                and is_hash(values.get("prefix_sha256"))
                and is_hash(values.get("before_cache_fingerprint_sha256"))
                and values.get("after_cache_fingerprint_sha256") == values["before_cache_fingerprint_sha256"]
                and all(type(values.get(k)) is int and values[k] > 0
                        for k in ("restored_tokens", "disk_read_bytes"))
                and values.get("correct") is True)

    def cache_state(row):
        values = row.get("values", {})
        first = sorted((r for r in rows if r["kind"] == "request" and r["turn"] == 0), key=lambda r: r["session"])
        counts = [r["receipt"]["cached_tokens"] for r in first]
        condition = values.get("condition")
        return (condition == run.get("cache_condition") and condition in ("cold_prefill", "warm_prefix")
                and values.get("request_ids") == [r["request_id"] for r in first]
                and values.get("cached_tokens") == counts
                and all(type(v) is int for v in counts)
                and (all(v == 0 for v in counts) if condition == "cold_prefill" else all(v > 0 for v in counts)))

    def runtime(row):
        values = row.get("values", {})
        expected = run.get("image_config_id")
        return (isinstance(expected, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is not None
                and values.get("observed_image_config_id") == expected
                and values.get("observed_source_sha256") == run.get("source_sha256")
                and isinstance(values.get("instance_ids"), list) and len(values["instance_ids"]) == 2
                and all(isinstance(v, str) and bool(v) for v in values["instance_ids"]))

    check("contract", contract)
    check("capacity", capacity)
    check("media", media)
    check("reasoning", reasoning)
    check("cache_state", cache_state)
    check("runtime", runtime)
    for event in ("eviction_restore", "restart_restore", "crash_restore"):
        check("durability", durable, event)
    for row in rows:
        if row["kind"] == "durability":
            require(row.get("event") in ("eviction_restore", "restart_restore", "crash_restore"),
                    "Unknown durability event; cold-prefill is not a durable restore")
    return checks


def validate(rows, artifact_root="."):
    result = {"schema_version": SCHEMA, "status": "fail", "workload_status": "fail",
              "capabilities": {}, "errors": [], "metrics": None, "adoption_status": "unknown"}
    try:
        require(isinstance(rows, list) and bool(rows), "No evidence")
        require(all(isinstance(r, dict) and r.get("schema_version") == SCHEMA and r.get("kind") in KINDS
                    for r in rows), "Unknown/malformed schema or record kind")
        runs = [r for r in rows if r["kind"] == "run"]
        require(len(runs) == 1 and rows[0] == runs[0], "Exactly one initial run record required")
        run = runs[0]
        require(run.get("image_config_id") is None or
                (isinstance(run["image_config_id"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", run["image_config_id"])),
                "Malformed expected image config ID")
        require(isinstance(run.get("run_id"), str) and bool(run["run_id"]), "Missing run ID")
        require(all(r.get("run_id") == run["run_id"] for r in rows), "Mixed run IDs")
        require(run.get("arm") in ("baseline", "candidate"), "Invalid run arm")
        require(isinstance(run.get("pair_id"), str) and bool(run["pair_id"]), "Missing pair ID")
        require(isinstance(run.get("model"), str) and bool(run["model"]), "Missing model")
        require(type(run.get("fixture_seed")) is int and type(run.get("request_seed")) is int, "Missing seeds")
        require(run.get("workload_version") == VERSION and run.get("contract") == CONTRACT, "Contract/version drift")
        policy = run.get("policy", {})
        require(policy == {**POLICY, "max_wall_regression_fraction": policy.get("max_wall_regression_fraction")}
                and number(policy.get("max_wall_regression_fraction"))
                and 0 <= policy["max_wall_regression_fraction"] <= 1, "Invalid predeclared tolerances")
        require(number(run.get("request_deadline_s")) and 0 < run["request_deadline_s"] <= 300,
                "Invalid request deadline")
        bundle = fixtures(run["fixture_seed"])
        require(run.get("fixture_sha256") == digest(bundle), "Fixture hash mismatch")
        require(is_hash(run.get("harness_sha256")), "Missing harness hash")
        for key in ("profile_sha256", "source_sha256"):
            require(run.get(key) is None or is_hash(run[key]), f"Malformed {key}")
        artifacts = run.get("source_artifacts")
        require((artifacts is None and run.get("source_sha256") is None) or
                (isinstance(artifacts, dict) and bool(artifacts) and all(is_hash(v) for v in artifacts.values())
                 and digest(artifacts) == run.get("source_sha256")), "Source artifact hash mismatch")
        require(run.get("cache_condition") in ("unknown", "cold_prefill", "warm_prefix"), "Missing cache condition declaration")
        requests = [r for r in rows if r["kind"] == "request"]
        caches = [r for r in rows if r["kind"] == "cache"]
        ends = [r for r in rows if r["kind"] == "run_end"]
        require(len(requests) == 12 and len(caches) == 12 and len(ends) == 1, "Incomplete four-session run")
        require(rows.index(ends[0]) > max(rows.index(r) for r in requests + caches), "Premature run_end")
        require(len({r.get("request_id") for r in requests}) == 12, "Duplicate/missing request IDs")
        require({(r.get("session"), r.get("turn")) for r in requests} == {(s, t) for s in range(4) for t in range(3)},
                "Missing/duplicate session turns")
        parsed = {}
        starts, ends_s, first_intervals, call_ids = [], [], [], []
        for session in bundle["sessions"]:
            history = session["messages"]
            previous_end = -1
            for turn in range(3):
                row = next(r for r in requests if r["session"] == session["id"] and r["turn"] == turn)
                require(type(row["session"]) is int and type(row["turn"]) is int, "Invalid session/turn type")
                require(isinstance(row.get("request_id"), str) and bool(row["request_id"]), "Missing request ID")
                expected_body = body(run["model"], run["request_seed"], history, turn)
                require(row.get("body") == expected_body and row.get("request_sha256") == digest(expected_body),
                        "Request drift or thinking/tool-ID history not preserved")
                require(row.get("error") is None and row.get("deadline_exceeded") is False, "Request failed/deadline")
                require(type(row.get("http_status")) is int and row["http_status"] == 200 and isinstance(row.get("content_type"), str)
                        and row["content_type"].split(";")[0].strip() == "text/event-stream", "Invalid HTTP receipt")
                require(number(row.get("elapsed_s")) and 0 < row["elapsed_s"] < run["request_deadline_s"]
                        and row.get("deadline_s") == run["request_deadline_s"], "Request exceeded total deadline")
                require(number(row.get("start_s")) and number(row.get("end_s"))
                        and 0 <= row["start_s"] < row["end_s"] and row["start_s"] >= previous_end
                        and abs(row["end_s"] - row["start_s"] - row["elapsed_s"]) < 0.02, "Invalid request timing")
                raw = base64.b64decode(row["response_b64"], validate=True)
                require(0 < len(raw) <= 4 * 1024 * 1024 and row.get("response_sha256") == sha256(raw),
                        "Response hash/size mismatch")
                receipt = parse(raw)
                require(receipt["usage"]["completion_tokens"] <= expected_body["max_tokens"]
                        and receipt["usage"]["prompt_tokens"] + receipt["usage"]["completion_tokens"] <= 1048576,
                        "Server usage exceeds request budget/window")
                parsed[row["request_id"]] = receipt
                require(row.get("receipt") == receipt, "Parsed receipt differs from retained SSE")
                require(row.get("server_usage") == receipt["usage"]
                        and row.get("server_cached_tokens") == receipt["cached_tokens"], "Server usage summary mismatch")
                history = advance(session, turn, receipt, history)
                cache = [c for c in caches if c.get("request_id") == row["request_id"]]
                require(len(cache) == 1 and cache[0].get("mode") == "unclassified"
                        and cache[0].get("cached_tokens") == receipt["cached_tokens"], "Invalid cache accounting receipt")
                starts.append(row["start_s"])
                ends_s.append(row["end_s"])
                previous_end = row["end_s"]
                if turn == 0:
                    first_intervals.append((row["start_s"], row["end_s"]))
                    call_ids.append(receipt["message"]["tool_calls"][0]["id"])
        require(len(set(call_ids)) == 4, "Tool call IDs collide across sessions")
        require(max(i[0] for i in first_intervals) < min(i[1] for i in first_intervals), "No observed four-request overlap")
        require(ends[0].get("completed_tasks") == 4 and type(ends[0].get("completed_tasks")) is int,
                "Missing affirmative four-task completion")
        wall = max(ends_s) - min(starts)
        require(number(ends[0].get("wall_s")) and abs(ends[0]["wall_s"] - wall) < 0.02, "Incorrect wall metric")
        result["workload_status"] = "pass"
        result["metrics"] = {"four_agent_tool_completion_wall_s": wall, "completed_tasks": 4,
                             "tasks_per_second": 4 / wall,
                             "server_prompt_tokens": sum(r["usage"]["prompt_tokens"] for r in parsed.values()),
                             "server_completion_tokens": sum(r["usage"]["completion_tokens"] for r in parsed.values()),
                             "server_cached_tokens": (sum(r["cached_tokens"] for r in parsed.values())
                                                      if all(r["cached_tokens"] is not None for r in parsed.values()) else None)}
        capabilities = capability_checks(rows, run, artifact_root)
        capabilities["profile_hash"] = "pass" if is_hash(run.get("profile_sha256")) else "unknown"
        capabilities["source_hash"] = "pass" if is_hash(run.get("source_sha256")) else "unknown"
        capabilities["cache_accounting"] = "pass" if result["metrics"]["server_cached_tokens"] is not None else "unknown"
        result["capabilities"] = capabilities
        result["status"] = ("fail" if "fail" in capabilities.values() else
                            "unknown" if "unknown" in capabilities.values() else "pass")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as error:
        result["errors"].append(str(error))
    return result


def compare(baseline, candidate, baseline_root=".", candidate_root="."):
    left, right = validate(baseline, baseline_root), validate(candidate, candidate_root)
    result = {"schema_version": SCHEMA, "status": "fail", "baseline": left, "candidate": right,
              "performance_status": "unknown", "errors": [],
              "token_equivalence_assumed": False, "adoption_status": "unknown",
              "improvement_status": "unknown", "comparison_scope": "single-pair"}
    try:
        require(left["workload_status"] == right["workload_status"] == "pass", "Both workloads must pass")
        a, b = baseline[0], candidate[0]
        require(a["arm"] == "baseline" and b["arm"] == "candidate", "Expected baseline then candidate")
        require(a["run_id"] != b["run_id"], "Cannot compare a run to itself")
        for key in ("pair_id", "fixture_sha256", "harness_sha256", "workload_version", "policy", "contract", "request_deadline_s", "cache_condition"):
            require(a[key] == b[key], f"Unmatched {key}")
        wall_a, wall_b = (v["metrics"]["four_agent_tool_completion_wall_s"] for v in (left, right))
        limit = a["policy"]["max_wall_regression_fraction"]
        result.update({"wall_ratio_candidate_over_baseline": wall_b / wall_a,
                       "observed_wall_improvement_fraction": 1 - wall_b / wall_a,
                       "predeclared_max_regression_fraction": limit,
                       "performance_status": "pass" if wall_b <= wall_a * (1 + limit) else "fail"})
        if result["performance_status"] == "pass" and any(v["capabilities"].get("cache_state") != "pass" for v in (left, right)):
            result["performance_status"] = "unknown"
        states = [left["status"], right["status"], result["performance_status"]]
        # A pair checks a declared non-regression limit. It cannot establish an
        # improvement beyond baseline variability or qualify the whole service.
        result["status"] = "fail" if "fail" in states else "unknown"
    except (ValueError, TypeError, KeyError, IndexError) as error:
        result["errors"].append(str(error))
    return result
