#!/usr/bin/env python3
"""Measure real SSE delivery for a long decode plus staggered short/long arrivals.

This complements SparkRun's simultaneous concurrency sweep. Unique prefixes
avoid accidental reuse across runs; receipts include overlap and delivery gaps.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import urllib.request
import uuid


def stream(url, prompt, limit, started=None):
    body = dict(model="GLM-5.3-Flash-EXL3", messages=[dict(role="user", content=prompt)],
                temperature=0, max_tokens=limit, stream=True,
                stream_options={"include_usage": True},
                chat_template_kwargs={"enable_thinking": False})
    return stream_request(url.rstrip("/") + "/v1/chat/completions", body, started)


def stream_request(url, body, started=None):
    started_unix = time.time()
    begin = time.monotonic()
    request = urllib.request.Request(url,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    arrivals, parts, usage, finish = [], [], None, None
    with urllib.request.urlopen(request, timeout=900) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if payload == b"[DONE]":
                break
            data = json.loads(payload)
            if data.get("error"):
                raise RuntimeError(data["error"])
            usage = data.get("usage") or usage
            for choice in data.get("choices", []):
                finish = choice.get("finish_reason") or finish
                text = choice.get("delta", {}).get("content") or choice.get("text") or ""
                if text:
                    arrivals.append(time.monotonic())
                    parts.append(text)
                    if started:
                        started.set()
    end = time.monotonic()
    if not arrivals or not finish:
        raise RuntimeError("Incomplete or empty SSE response")
    return dict(started_unix=started_unix, start=begin, first=arrivals[0], end=end, ttft_s=arrivals[0]-begin,
                wall_s=end-begin, max_delivery_gap_s=max([b-a for a,b in zip(arrivals, arrivals[1:])] or [0]),
                text="".join(parts), usage=usage, finish_reason=finish)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://10.32.21.31:8888")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    results = []
    for label, filler in [("short", ""), ("prefill36k", "The orchard grows apples and pears. " * 4000)]:
        nonce = str(uuid.uuid4())
        ready = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(stream, args.url, nonce + " Explain the design of a database storage engine in detail, with examples. Write at least 2000 words.", 1024, ready)
            if not ready.wait(120):
                raise RuntimeError("Long decode did not produce content in 120 seconds")
            time.sleep(2)
            new = pool.submit(stream, args.url, nonce + "-new " + filler + " What is 17 times 19? Reply with only the integer.", 32)
            newcomer, original = new.result(), old.result()
        overlap = newcomer["first"] < original["end"]
        passed = newcomer["text"].strip() == "323"
        if label == "short":
            passed = passed and overlap and newcomer["ttft_s"] < 5
        results.append(dict(workload=label, original=original, newcomer=newcomer,
                            first_token_before_original_finished=overlap, passed=passed))
        args.out.write_text(json.dumps(results, indent=2) + "\n")
        print(label, "PASS" if passed else "FAIL", round(newcomer["ttft_s"], 3), flush=True)
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
