#!/usr/bin/env python3
"""Fail-closed TensorFold health/slot gate and shape warmup for SparkRun."""
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


def check_health(body, slots, context):
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise RuntimeError(f"TensorFold engine is unhealthy: {body!r}")
    if body.get("mode") != "strict":
        raise RuntimeError("TensorFold strict health is required")
    if body.get("streams", {}).get("max") != slots:
        raise RuntimeError(f"Expected {slots} loaded slots, got {body.get('streams')!r}")
    if body.get("context_length") != context:
        raise RuntimeError(f"Expected {context} context, got {body.get('context_length')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", default="GLM-5.3-Flash-EXL3")
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--context", type=int, default=850000)
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    before = fetch(url + "/health")
    check_health(before, args.slots, args.context)
    print("TensorFold loaded health:", json.dumps(before, sort_keys=True), flush=True)

    def warmup(pair):
        length, rank = pair
        started = time.monotonic()
        result = fetch(url + "/v1/chat/completions", {
            "model": args.model, "messages": [{"role": "user", "content":
                f"Warmup stream {rank}. " + "context " * length +
                "\nWhat is 17 times 19? Reply with only the integer."}],
            "max_tokens": 32, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        if result.get("error"):
            raise RuntimeError(result["error"])
        choices = result.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise RuntimeError(f"Incomplete warmup: {result!r}")
        if choices[0].get("message", {}).get("content", "").strip() != "323":
            raise RuntimeError(f"Incorrect warmup: {result!r}")
        usage = result.get("usage", {})
        if not usage.get("prompt_tokens") or not usage.get("completion_tokens"):
            raise RuntimeError(f"Missing warmup usage: {result!r}")
        return {"stream": rank, "seconds": round(time.monotonic() - started, 3), "usage": usage}

    for length in (128, 4096, 16384):
        with ThreadPoolExecutor(max_workers=args.slots) as pool:
            results = list(pool.map(warmup, [(length, rank) for rank in range(args.slots)]))
        print("TensorFold warmup:", json.dumps({"words": length, "results": results}), flush=True)
        check_health(fetch(url + "/health"), args.slots, args.context)
    print("TensorFold readiness and shape warmup passed", flush=True)


if __name__ == "__main__":
    main()
