#!/usr/bin/env python3
"""Check /v1/models discovery fields against the launch settings and, with --completions, against what replies echo."""
import argparse
import json
import urllib.request

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# Qwen's template levels and the pinned engine's coercion; upstream discovery no longer advertises a map.
SUPPORTED_EFFORTS = ["xhigh", "medium", "low", "none"]
QWEN_EFFORT_MAP = dict(zip(EFFORTS, ("none", "low", "low", "medium", "xhigh", "xhigh", "xhigh")))


def fetch(url, body=None, timeout=120):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_entry(entry, context, default_effort=None, default_enabled=True):
    """The fields a client configures itself from; returns the reasoning object."""
    for field in ("context_length", "max_model_len"):
        if entry.get(field) != context:
            raise RuntimeError(f"Expected {field} {context}, got {entry.get(field)!r}")
    if "default_max_tokens" in entry:
        raise RuntimeError(f"Unexpected default_max_tokens: {entry['default_max_tokens']!r}")
    reasoning = entry.get("reasoning")
    if not isinstance(reasoning, dict) or not isinstance(reasoning.get("default_enabled"), bool):
        raise RuntimeError(f"Missing reasoning.default_enabled: {reasoning!r}")
    expected = {"default_enabled": default_enabled, "supported_efforts": SUPPORTED_EFFORTS}
    if default_effort is not None:
        expected["default_effort"] = default_effort
    if reasoning != expected:
        raise RuntimeError(f"Expected reasoning {expected!r}, got {reasoning!r}")
    return reasoning


def check_echo(reasoning, effort, reply):
    """A chat reply's echo against the advertised default or the pinned Qwen effort coercion."""
    echo = reply.get("tensorfold") or {}
    if effort is None:
        if echo.get("enable_thinking") != reasoning["default_enabled"]:
            raise RuntimeError(f"default_enabled {reasoning['default_enabled']!r}, reply echoed {echo.get('enable_thinking')!r}")
        expected = reasoning.get("default_effort")
    else:
        expected = QWEN_EFFORT_MAP[effort]
    if echo.get("reasoning_effort") != expected:
        raise RuntimeError(f"Effort {effort!r}: expected {expected!r}, reply echoed {echo.get('reasoning_effort')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", default="Qwen3.8-Flash-Next")
    parser.add_argument("--context", type=int, default=1048576)
    parser.add_argument("--default-effort", choices=SUPPORTED_EFFORTS,
                        help="Expected resolved --reasoning-effort default; otherwise require the field to be absent")
    parser.add_argument("--disable-thinking", action="store_true", help="Expect the server's thinking default to be off")
    parser.add_argument("--completions", action="store_true",
                        help="Also send eight 8-token chat requests: one naming no effort, one per accepted effort")
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    entries = [entry for entry in fetch(url + "/v1/models")["data"] if entry.get("id") == args.model]
    if len(entries) != 1:
        raise RuntimeError(f"Expected one {args.model} entry, got {entries!r}")
    reasoning = check_entry(entries[0], args.context, args.default_effort, not args.disable_thinking)
    print("Model entry:", json.dumps(entries[0], sort_keys=True), flush=True)
    if args.completions:
        for effort in (None, *EFFORTS):
            body = {"model": args.model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 8}
            if effort is not None:
                body["reasoning_effort"] = effort
            reply = fetch(url + "/v1/chat/completions", body)
            check_echo(reasoning, effort, reply)
            print(f"Effort {effort!r} echoed {reply['tensorfold'].get('reasoning_effort')!r}, "
                  f"thinking {reply['tensorfold'].get('enable_thinking')!r}", flush=True)
    print("Model discovery checks passed", flush=True)


if __name__ == "__main__":
    main()
