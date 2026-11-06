#!/usr/bin/env python3
"""Bounded four-session runner and offline GLM evidence validation/comparison."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from .evidence import SCHEMA, advance, compare, validate
from .fixtures import CONTRACT, POLICY, VERSION, body, canonical, digest, fixtures, sha256, write_fixtures
from .transport import endpoint, parse, request, strict_json


def harness_hash():
    return digest({p.name: sha256(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))})


def source_artifacts(paths):
    if not paths:
        return None
    require_unique = [Path(p).name for p in paths]
    if len(set(require_unique)) != len(require_unique):
        raise ValueError("Source artifact basenames must be unique")
    return {Path(p).name: sha256(Path(p).read_bytes()) for p in paths}


def run(args):
    endpoint(args.endpoint)  # Fail before making an output file for invalid endpoints.
    bundle = fixtures(args.fixture_seed)
    artifacts = source_artifacts(args.source)
    run_id = str(uuid.uuid4())
    header = {"kind": "run", "run_id": run_id, "schema_version": SCHEMA,
              "created_at": datetime.now(timezone.utc).isoformat(), "arm": args.arm,
              "pair_id": args.pair_id, "endpoint": args.endpoint, "model": args.model,
              "fixture_seed": args.fixture_seed, "request_seed": args.request_seed,
              "fixture_sha256": digest(bundle), "workload_version": VERSION,
              "harness_sha256": harness_hash(),
              "profile_sha256": sha256(Path(args.profile).read_bytes()) if args.profile else None,
              "source_sha256": digest(artifacts) if artifacts else None,
              "source_artifacts": artifacts, "contract": CONTRACT,
              "image_config_id": getattr(args, "image_config", None),
              "cache_condition": getattr(args, "cache_condition", "unknown"),
              "policy": {**POLICY, "max_wall_regression_fraction": args.max_wall_regression},
              "request_deadline_s": args.request_deadline,
              "payload_token_count": None, "media_exercised": False,
              "capacity_exercised": False, "durability_exercised": False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)
    origin = time.monotonic()
    with output.open("x", encoding="utf-8") as report:
        def write(row):
            record = {"schema_version": SCHEMA, "run_id": run_id, **row}
            with lock:
                rows.append(record)
                report.write(canonical(record).decode() + "\n")
                report.flush()

        write(header)

        def session_task(session):
            history = session["messages"]
            barrier.wait(timeout=10)
            for turn in range(3):
                payload = body(args.model, args.request_seed, history, turn)
                started = time.monotonic() - origin
                row = request(args.endpoint, payload, args.request_deadline,
                              os.environ.get(args.api_key_env) if args.api_key_env else None)
                row.update({"kind": "request", "request_id": f"{run_id}/{session['id']}/{turn}",
                            "session": session["id"], "turn": turn, "body": payload,
                            "request_sha256": digest(payload), "start_s": started,
                            "receipt": None, "correct": False})
                next_history = None
                try:
                    if row["error"] is not None:
                        raise ValueError(row["error"])
                    row["receipt"] = parse(base64.b64decode(row["response_b64"]))
                    next_history = advance(session, turn, row["receipt"], history)
                    row["correct"] = True
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    row["error"] = row["error"] or f"{type(error).__name__}: {error}"
                ended = time.monotonic() - origin
                row.update(end_s=ended, elapsed_s=ended - started,
                           server_usage=row["receipt"]["usage"] if row["receipt"] else None,
                           server_cached_tokens=row["receipt"]["cached_tokens"] if row["receipt"] else None)
                write(row)
                write({"kind": "cache", "request_id": row["request_id"], "mode": "unclassified",
                       "cached_tokens": row["receipt"]["cached_tokens"] if row["receipt"] else None,
                       "durable_restore": None})
                if not row["correct"]:
                    return False
                history = next_history
            return True

        with ThreadPoolExecutor(max_workers=4) as pool:
            completed = sum(pool.map(session_task, bundle["sessions"]))
        requests = [r for r in rows if r["kind"] == "request"]
        wall = max(r["end_s"] for r in requests) - min(r["start_s"] for r in requests)
        write({"kind": "run_end", "completed_tasks": completed, "wall_s": wall})
        os.fsync(report.fileno())
    return {"run_id": run_id, "output": str(output), **validate(rows, output.parent)}


def load(path):
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for index, line in enumerate(source, 1):
            if not line.strip():
                raise ValueError(f"{path}:{index}: blank JSONL record")
            row = strict_json(line)
            if isinstance(row, dict) and isinstance(row.get("provenance"), dict):
                artifact = row["provenance"].get("artifact_path")
                if isinstance(artifact, str) and artifact and not Path(artifact).is_absolute():
                    row["provenance"]["artifact_path"] = str((Path(path).parent / artifact).resolve())
            rows.append(row)
    return rows


def positive_deadline(value):
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 300:
        raise argparse.ArgumentTypeError("deadline must be finite and in (0, 300] seconds")
    return result


def tolerance(value):
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("regression fraction must be finite and in [0, 1]")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixtures", help="Export text/tool manifest and 50 PNG fixtures without network access")
    fixture.add_argument("--output", required=True)
    fixture.add_argument("--seed", type=int, default=20261106)
    runner = sub.add_parser("run", help="Send only 12 bounded streamed requests to an explicit endpoint")
    runner.add_argument("--endpoint", required=True)
    runner.add_argument("--model", required=True)
    runner.add_argument("--output", required=True)
    runner.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    runner.add_argument("--pair-id", required=True)
    runner.add_argument("--profile", help="Profile bytes to hash; omitted means unknown")
    runner.add_argument("--image-config", help="Expected immutable image config ID; requires independent runtime observation")
    runner.add_argument("--source", action="append", default=[], help="Source/lock artifact to hash (repeatable); omitted means unknown")
    runner.add_argument("--fixture-seed", type=int, default=20261106)
    runner.add_argument("--request-seed", type=int, default=17)
    runner.add_argument("--request-deadline", type=positive_deadline, default=60.0)
    runner.add_argument("--max-wall-regression", type=tolerance, default=POLICY["max_wall_regression_fraction"])
    runner.add_argument("--api-key-env", help="Environment variable name; credentials are never recorded")
    runner.add_argument("--cache-condition", choices=("unknown", "cold_prefill", "warm_prefix"), default="unknown",
                        help="Predeclare starting cache condition; requires separate observed cache_state evidence")
    validator = sub.add_parser("validate", help="Reparse receipts and validate workload plus full contract offline")
    validator.add_argument("run")
    validator.add_argument("--evidence", action="append", default=[], help="Additional same-run JSONL observations")
    comparison = sub.add_parser("compare", help="Compare matched baseline/candidate receipts offline")
    comparison.add_argument("baseline")
    comparison.add_argument("candidate")
    comparison.add_argument("--baseline-evidence", action="append", default=[])
    comparison.add_argument("--candidate-evidence", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "fixtures":
            result = {"status": "pass", **write_fixtures(Path(args.output), args.seed)}
        elif args.command == "run":
            if args.image_config:
                import re
                if re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_config) is None:
                    raise ValueError("Expected sha256:<64 lowercase hex digits> image config ID")
            if args.api_key_env and not os.environ.get(args.api_key_env):
                raise ValueError("Specified API key environment variable is empty/unset")
            result = run(args)
        elif args.command == "validate":
            rows = load(args.run)
            for path in args.evidence:
                rows.extend(load(path))
            result = validate(rows, Path(args.run).parent)
        else:
            baseline, candidate = load(args.baseline), load(args.candidate)
            for path in args.baseline_evidence:
                baseline.extend(load(path))
            for path in args.candidate_evidence:
                candidate.extend(load(path))
            result = compare(baseline, candidate, Path(args.baseline).parent, Path(args.candidate).parent)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        result = {"status": "fail", "errors": [f"{type(error).__name__}: {error}"]}
    print(json.dumps(result, indent=2, allow_nan=False))
    return {"pass": 0, "fail": 1, "unknown": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
