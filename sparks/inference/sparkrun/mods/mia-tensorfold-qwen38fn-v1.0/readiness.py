#!/usr/bin/env python3
"""Fail-closed tensorfold-native health/lane gate and concurrent smoke test for SparkRun."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
import urllib.request


def fetch(url, body=None, timeout=240):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_health(body, model, lanes):
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise RuntimeError(f"TensorFold engine is unhealthy: {body!r}")
    if body.get("warming") is not False:
        raise RuntimeError(f"TensorFold engine is still warming: {body!r}")
    if body.get("model") != model:
        raise RuntimeError(f"Expected model {model}, got {body.get('model')!r}")
    if body.get("max_batch_size") != lanes:
        raise RuntimeError(f"Expected {lanes} loaded lanes, got {body.get('max_batch_size')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", required=True)
    parser.add_argument("--lanes", type=int, default=16)
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    before = fetch(url + "/health")
    check_health(before, args.model, args.lanes)
    print("TensorFold loaded health:", json.dumps(before, sort_keys=True), flush=True)

    def smoke(lane):
        started = time.monotonic()
        result = fetch(url + "/v1/chat/completions", {
            "model": args.model, "messages": [{"role": "user", "content":
                f"Smoke stream {lane}. What is 17 times 19? Reply with only the integer."}],
            "max_tokens": 32, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        if result.get("error"):
            raise RuntimeError(result["error"])
        choices = result.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise RuntimeError(f"Incomplete smoke reply: {result!r}")
        if choices[0].get("message", {}).get("content", "").strip() != "323":
            raise RuntimeError(f"Incorrect smoke reply: {result!r}")
        usage = result.get("usage", {})
        if not usage.get("prompt_tokens") or not usage.get("completion_tokens"):
            raise RuntimeError(f"Missing smoke usage: {result!r}")
        return {"stream": lane, "seconds": round(time.monotonic() - started, 3), "usage": usage}

    with ThreadPoolExecutor(max_workers=args.lanes) as pool:
        results = list(pool.map(smoke, range(args.lanes)))
    print("TensorFold smoke:", json.dumps(results), flush=True)
    check_health(fetch(url + "/health"), args.model, args.lanes)
    print("TensorFold readiness and concurrent smoke test passed", flush=True)


if __name__ == "__main__":
    main()
