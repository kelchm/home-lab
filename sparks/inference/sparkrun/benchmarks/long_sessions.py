#!/usr/bin/env python3
"""Bounded, identical-payload long-session comparison against a SparkRun instance.

Run on the head with tokenizers installed. Reports stay outside git. Native
SparkRun owns the server; this client measures cache continuations and arrivals
that the native throughput profile does not cover.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

from tokenizers import Tokenizer


class Campaign:
    def __init__(self, args):
        self.args = args
        self.tokenizer = Tokenizer.from_file(args.tokenizer)
        self.output = Path(args.output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry = self.output.with_suffix(".memory.jsonl")
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.minimum = {}
        self.failures = []
        self.lock = threading.Lock()
        self.memory = threading.Thread(target=self.monitor, daemon=True)

    def monitor(self):
        low = {}
        link_baseline = {}
        paths = ["/proc/meminfo"]
        if self.args.require_stable_links:
            paths += [f"/sys/class/net/enP7s7/{key}" for key in ("carrier", "speed", "carrier_down_count")]
        while not self.finished.is_set():
            for host in ("head", self.args.worker):
                try:
                    raw = "\n".join(Path(path).read_text().strip() for path in paths) if host == "head" else subprocess.check_output(
                        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "cat", *paths],
                        text=True, timeout=10)
                    link = None
                    if self.args.require_stable_links:
                        raw, carrier, speed, drops = raw.rstrip().rsplit("\n", 3)
                        link = {"carrier": int(carrier), "speed": int(speed), "carrier_down_count": int(drops)}
                    values = {line.split(":")[0]: int(line.split()[1]) for line in raw.splitlines()}
                    gib = values["MemAvailable"] / 1024 ** 2
                    self.minimum[host] = min(self.minimum.get(host, gib), gib)
                    with self.telemetry.open("a") as report:
                        report.write(json.dumps({"time": time.time(), "host": host, "available_gib": gib,
                                                 "swap_used_gib": (values["SwapTotal"] - values["SwapFree"]) / 1024 ** 2,
                                                 **({"link": link} if link else {})}) + "\n")
                    if link is not None:
                        baseline = link_baseline.setdefault(host, link["carrier_down_count"])
                        if link["carrier"] != 1 or link["speed"] != 10000 or link["carrier_down_count"] != baseline:
                            raise RuntimeError(f"{host} management link changed: {link}")
                    low[host] = low.get(host, 0) + 1 if gib < 2 else 0
                    if low[host] >= 3:
                        raise RuntimeError(f"{host} MemAvailable below 2 GiB for three samples")
                except Exception as error:
                    self.failures.append(f"Campaign monitoring failed: {error}")
                    self.cancel.set()
                    # Evaluation abort only; never replace a stack or launch a watchdog.
                    subprocess.run(["sparkrun", "stop", self.args.recipe, "--cluster", "sparks"],
                                   timeout=120, check=False)
                    return
            self.finished.wait(5)

    def context(self, count, salt):
        prefix = f"# Repository snapshot {salt}\n# REVIEW_SECRET = {self.args.secret}\n"
        block = ("# src/normalization.py\ndef normalize_record(record):\n"
                 "    name = record.get('name', '').strip()\n"
                 "    tags = sorted(set(record.get('tags', [])))\n"
                 "    return {'name': name, 'tags': tags, 'active': bool(record.get('active', False))}\n\n")
        target = count - len(self.tokenizer.encode(prefix).ids)
        text = block * (target // len(self.tokenizer.encode(block).ids) + 8)
        tokens = self.tokenizer.encode(text).ids[:target]
        # Decode once; the measured API prompt count includes the chat template.
        return prefix + self.tokenizer.decode(tokens, skip_special_tokens=False)

    def request(self, content, limit=256, expected=None):
        body = {"model": self.args.model, "messages": [{"role": "user", "content": content}],
                "temperature": 0, "max_tokens": limit, "stream": True,
                "stream_options": {"include_usage": True},
                "chat_template_kwargs": {"enable_thinking": False}}
        started = time.monotonic()
        first = None
        last = None
        gaps = []
        chunks = []
        usage = None
        engine_stats = None
        finish = None
        request = urllib.request.Request(self.args.url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.args.timeout) as response:
            for line in response:
                if self.cancel.is_set():
                    raise RuntimeError("Campaign aborted: " + self.failures[-1])
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                data = json.loads(payload)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                usage = data.get("usage") or usage
                engine_stats = data.get("tensorfold") or engine_stats
                for choice in data.get("choices", []):
                    finish = choice.get("finish_reason") or finish
                    value = choice.get("delta", {}).get("content") or ""
                    if value:
                        now = time.monotonic()
                        if first is None:
                            first = now
                        if last is not None:
                            gaps.append(now - last)
                        last = now
                        chunks.append(value)
        ended = time.monotonic()
        text = "".join(chunks)
        if not finish or first is None or usage is None:
            raise RuntimeError(f"Incomplete response: finish={finish!r}, usage={usage!r}, text={text[:200]!r}")
        if expected is not None and text.strip() != expected:
            raise RuntimeError(f"Incorrect answer: {text[:300]!r}; expected {expected!r}")
        if expected is None and self.args.secret not in text:
            raise RuntimeError(f"Long response lost REVIEW_SECRET: {text[:300]!r}")
        completion = usage.get("completion_tokens", 0)
        return {"start": started, "first": first, "end": ended, "ttft_s": first - started,
                "elapsed_s": ended - started, "decode_s": ended - first,
                "completion_tps": completion / max(ended - first, 0.001),
                "max_chunk_gap_s": max(gaps, default=0), "usage": usage, "finish_reason": finish,
                "text": text, "payload_tokens": len(self.tokenizer.encode(content).ids),
                "engine_stats": engine_stats}

    def write(self, name, rows, wall):
        total = sum(row["usage"]["completion_tokens"] for row in rows)
        result = {"case": name, "arm": self.args.arm, "wall_s": wall, "streams": rows,
                  "aggregate_e2e_tps": total / wall,
                  "slowest_ttft_s": max(row["ttft_s"] for row in rows),
                  "memory_min_gib": dict(self.minimum)}
        with self.output.open("a") as report:
            report.write(json.dumps(result) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key != "streams"}), flush=True)

    def phase(self, name, contexts, suffix, limit=256, expected=None):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(contexts)) as pool:
            futures = [pool.submit(self.request, context + suffix, limit, expected) for context in contexts]
            rows = [future.result() for future in futures]
        self.write(name, rows, time.monotonic() - started)
        return rows

    def run(self):
        self.memory.start()
        try:
            # Fixed salts/secret make payloads identical between engines and boots.
            suffix = ("\nExplain the design of the normalization function and give 20 detailed examples. "
                      "Start by quoting REVIEW_SECRET exactly. Write at least 2000 words.")
            lengths = {"full": [131072, 250000], "repeat": [131072],
                       "250k": [250000], "ceiling": []}[self.args.suite]
            for count in lengths:
                contexts = [self.context(count, f"{self.args.campaign}-{count}-{index}") for index in range(4)]
                if self.args.suite != "repeat" and count == 131072:
                    self.phase("c1-128k-cold", contexts[:1], suffix)
                    self.phase("c2-128k-cold", contexts[1:3], suffix)
                    # Change salts so C4 cold is not warmed by the controls.
                    contexts = [self.context(count, f"{self.args.campaign}-c4-{count}-{index}") for index in range(4)]
                self.phase(f"c4-{count}-cold", contexts, suffix)
                self.phase(f"c4-{count}-warm", contexts, suffix)
                if count == 131072 and self.args.suite != "repeat":
                    edited = [value[:len(value)//2] + "\n# Newly edited module: tag ordering is unchanged.\n" +
                              value[len(value)//2:] for value in contexts]
                    self.phase("c4-128k-edit", edited, "\nReply with only REVIEW_SECRET.", 64, self.args.secret)
                    self.phase("c4-128k-fork", contexts, "\nA new branch: reply with only REVIEW_SECRET.", 64, self.args.secret)
            if self.args.suite == "full":
                shared = self.context(131072, self.args.campaign + "-shared")
                self.phase("c4-128k-shared-cold", [shared] * 4, suffix)
                self.phase("c4-128k-shared-warm", [shared] * 4, suffix)
                boundary = [self.context(262144, f"{self.args.campaign}-boundary-{index}") for index in range(4)]
                self.phase("c4-262k-admission-boundary", boundary, "\nReply with only REVIEW_SECRET.", 64, self.args.secret)
                long = [self.context(131072, f"{self.args.campaign}-arrival-{index}") for index in range(2)]
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(self.request, value + suffix) for value in long]
                    time.sleep(0.5)
                    short = pool.submit(self.request, "What is 17 times 19? Reply with only the integer.", 32, "323")
                    rows = [future.result() for future in futures] + [short.result()]
                self.write("short-arrival-during-long-prefill", rows, time.monotonic() - started)
            if self.args.suite == "ceiling":
                near = self.context(849700, self.args.campaign + "-ceiling")
                self.phase("c1-near-850k", [near], "\nReply with only REVIEW_SECRET.", 64, self.args.secret)
        finally:
            self.finished.set()
            self.memory.join(timeout=15)
            if self.failures:
                raise RuntimeError("; ".join(self.failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--worker", default="kelchm@10.32.21.32")
    parser.add_argument("--url", default="http://127.0.0.1:8888/v1")
    parser.add_argument("--model", default="GLM-5.3-Flash-EXL3")
    parser.add_argument("--suite", choices=("full", "repeat", "250k", "ceiling"), default="full")
    parser.add_argument("--campaign", default="glm53-20260930-a")
    parser.add_argument("--secret", default="cobalt-river-7391")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--require-stable-links", action="store_true",
                        help="Abort on lost 10GbE carrier or new carrier drops on enP7s7 on either Spark")
    Campaign(parser.parse_args()).run()


if __name__ == "__main__":
    main()
