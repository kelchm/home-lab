#!/usr/bin/env python3
"""Check /v1/models discovery fields against the launch settings and, with --completions, against what replies echo."""
import argparse
import json
import urllib.request

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def fetch(url, body=None, timeout=120):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_entry(entry, context, max_tokens):
    """The fields a client configures itself from; returns the reasoning object."""
    for field in ("context_length", "max_model_len"):
        if entry.get(field) != context:
            raise RuntimeError(f"Expected {field} {context}, got {entry.get(field)!r}")
    if entry.get("default_max_tokens") != max_tokens:
        raise RuntimeError(f"Expected default_max_tokens {max_tokens}, got {entry.get('default_max_tokens')!r}")
    reasoning = entry.get("reasoning")
    if not isinstance(reasoning, dict) or not isinstance(reasoning.get("default_enabled"), bool):
        raise RuntimeError(f"Missing reasoning.default_enabled: {reasoning!r}")
    heard = reasoning.get("effort_map")
    if not isinstance(heard, dict) or set(heard) != set(EFFORTS):
        raise RuntimeError(f"effort_map does not cover the accepted efforts: {heard!r}")
    supported = reasoning.get("supported_efforts")
    if not isinstance(supported, list) or len(set(supported)) != len(supported) or set(supported) != set(heard.values()):
        raise RuntimeError(f"supported_efforts {supported!r} is not the levels effort_map resolves to")
    if "default_effort" in reasoning and reasoning["default_effort"] not in supported:
        raise RuntimeError(f"default_effort {reasoning['default_effort']!r} is not a supported effort")
    return reasoning


def check_echo(reasoning, effort, reply):
    """A chat reply's echo against the advertised default (effort None) or the advertised mapping."""
    echo = reply.get("tensorfold") or {}
    if effort is None:
        if echo.get("enable_thinking") != reasoning["default_enabled"]:
            raise RuntimeError(f"default_enabled {reasoning['default_enabled']!r}, reply echoed {echo.get('enable_thinking')!r}")
        expected = reasoning.get("default_effort")
    else:
        expected = reasoning["effort_map"][effort]
    if echo.get("reasoning_effort") != expected:
        raise RuntimeError(f"Effort {effort!r}: advertised {expected!r}, reply echoed {echo.get('reasoning_effort')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", default="Qwen3.8-Flash-Next")
    parser.add_argument("--context", type=int, default=1048576)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--completions", action="store_true",
                        help="Also send eight 8-token chat requests: one naming no effort, one per accepted effort")
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    entries = [entry for entry in fetch(url + "/v1/models")["data"] if entry.get("id") == args.model]
    if len(entries) != 1:
        raise RuntimeError(f"Expected one {args.model} entry, got {entries!r}")
    reasoning = check_entry(entries[0], args.context, args.max_tokens)
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
