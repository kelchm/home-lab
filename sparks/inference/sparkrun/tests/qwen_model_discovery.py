#!/usr/bin/env python3
"""Check /v1/models discovery fields against the launch settings and, with --completions, against what replies echo."""
import argparse
import json
import urllib.request

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# The pinned checkpoint's template levels, highest first, and the switch.
PROFILE_EFFORTS = ["xhigh", "medium", "low", "none"]


def fetch(url, body=None, timeout=120):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_entry(entry, context, efforts=PROFILE_EFFORTS):
    """The fields a client configures itself from; returns the reasoning object."""
    for field in ("context_length", "max_model_len"):
        if entry.get(field) != context:
            raise RuntimeError(f"Expected {field} {context}, got {entry.get(field)!r}")
    if "default_max_tokens" in entry:
        raise RuntimeError(f"Unexpected default_max_tokens: {entry['default_max_tokens']!r}")
    reasoning = entry.get("reasoning")
    if not isinstance(reasoning, dict) or not isinstance(reasoning.get("default_enabled"), bool):
        raise RuntimeError(f"Missing reasoning.default_enabled: {reasoning!r}")
    supported = reasoning.get("supported_efforts")
    if not isinstance(supported, list) or not supported or len(set(supported)) != len(supported) or not set(supported) <= set(EFFORTS):
        raise RuntimeError(f"supported_efforts {supported!r} is not a list of distinct accepted efforts")
    if supported != efforts:
        raise RuntimeError(f"supported_efforts {supported!r}, expected {efforts!r}")
    if "default_effort" in reasoning and reasoning["default_effort"] not in supported:
        raise RuntimeError(f"default_effort {reasoning['default_effort']!r} is not a supported effort")
    return reasoning


def check_echo(reasoning, effort, reply):
    """A chat reply's echo against the advertised default (effort None) or the advertised levels."""
    echo = reply.get("tensorfold") or {}
    heard = echo.get("reasoning_effort")
    if effort is None:
        if echo.get("enable_thinking") != reasoning["default_enabled"]:
            raise RuntimeError(f"default_enabled {reasoning['default_enabled']!r}, reply echoed {echo.get('enable_thinking')!r}")
        if heard != reasoning.get("default_effort"):
            raise RuntimeError(f"default_effort {reasoning.get('default_effort')!r}, reply echoed {heard!r}")
        return
    supported = reasoning["supported_efforts"]
    if heard not in supported or (effort in supported and heard != effort):
        raise RuntimeError(f"Effort {effort!r}: advertised {supported!r}, reply echoed {heard!r}")
    if echo.get("enable_thinking") != (heard != "none"):
        raise RuntimeError(f"Effort {effort!r} echoed {heard!r} with thinking {echo.get('enable_thinking')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", default="Qwen3.8-Flash-Next")
    parser.add_argument("--context", type=int, default=1048576)
    parser.add_argument("--completions", action="store_true",
                        help="Also send eight 8-token chat requests: one naming no effort, one per accepted effort")
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    entries = [entry for entry in fetch(url + "/v1/models")["data"] if entry.get("id") == args.model]
    if len(entries) != 1:
        raise RuntimeError(f"Expected one {args.model} entry, got {entries!r}")
    reasoning = check_entry(entries[0], args.context)
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
