#!/usr/bin/env python3
"""Fail-closed TensorFold readiness gate shared by every Spark profile.

Checks the engine's health in its own format, the /v1/models entry the profile promises, one concurrent smoke reply per
stream, one tool call and, where the server renders prompts through /tokenize, that each advertised reasoning level
reaches the prompt differently and that every other standard level name maps onto one of them.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
import urllib.error
import urllib.request

STANDARD_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
PROMPT = [{"role": "user", "content": "Readiness render. Reply with the word ready."}]


def fetch(url, body=None, timeout=240):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_health(body, model, lanes, context, min_pool_tokens=0):
    """Zig servers report status/warming/max_batch_size; Mia's Python server reports ok/mode/streams/pool_tokens."""
    if not isinstance(body, dict):
        raise RuntimeError(f"Health is not a JSON object: {body!r}")
    if "ok" in body:
        if body["ok"] is not True or body.get("mode") != "strict":
            raise RuntimeError(f"Engine is not healthy in strict mode: {body!r}")
        if body.get("streams", {}).get("max") != lanes:
            raise RuntimeError(f"Expected {lanes} loaded streams, got {body.get('streams')!r}")
        if body.get("context_length") != context:
            raise RuntimeError(f"Health reports context {body.get('context_length')!r}, not {context}")
        pool = body.get("pool_tokens")
        if min_pool_tokens and (type(pool) is not int or pool < min_pool_tokens):
            raise RuntimeError(f"Expected at least {min_pool_tokens} pool tokens, got {pool!r}")
        return
    if body.get("status") != "ok" or body.get("warming") is not False:
        raise RuntimeError(f"Engine is unhealthy or still warming: {body!r}")
    if body.get("model") != model:
        raise RuntimeError(f"Health names model {body.get('model')!r}, not {model}")
    if body.get("max_batch_size") != lanes:
        raise RuntimeError(f"Expected {lanes} loaded lanes, got {body.get('max_batch_size')!r}")


def check_models(body, model, context, efforts):
    entries = [entry for entry in body.get("data", []) if entry.get("id") == model]
    if len(entries) != 1:
        raise RuntimeError(f"/v1/models does not list {model} once: {body!r}")
    entry = entries[0]
    if entry.get("owned_by") != "tensorfold":
        raise RuntimeError(f"/v1/models owned_by is {entry.get('owned_by')!r}, not tensorfold")
    for field in ("context_length", "max_model_len"):
        if entry.get(field) != context:
            raise RuntimeError(f"/v1/models {field} is {entry.get(field)!r}, not {context}")
    reported = (entry.get("reasoning") or {}).get("supported_efforts") or []
    if list(reported) != list(efforts):
        raise RuntimeError(f"/v1/models supported_efforts is {reported!r}, not {list(efforts)!r}")
    return entry


def chat(url, body):
    result = fetch(url + "/v1/chat/completions", body)
    if result.get("error"):
        raise RuntimeError(result["error"])
    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices: {result!r}")
    return result, choices[0]


def smoke(url, model, lane):
    started = time.monotonic()
    result, choice = chat(url, {
        "model": model, "messages": [{"role": "user", "content":
            f"Smoke stream {lane}. What is 17 times 19? Reply with only the integer."}],
        "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
    })
    if choice.get("finish_reason") != "stop" or (choice.get("message", {}).get("content") or "").strip() != "323":
        raise RuntimeError(f"Incorrect smoke reply: {result!r}")
    usage = result.get("usage", {})
    if not usage.get("prompt_tokens") or not usage.get("completion_tokens"):
        raise RuntimeError(f"Missing smoke usage: {result!r}")
    return {"stream": lane, "seconds": round(time.monotonic() - started, 3), "usage": usage}


def warmup(url, model, lanes, words):
    """Concurrent prompts of increasing length, for engines that size kernels on first use."""
    def one(lane):
        result, choice = chat(url, {
            "model": model, "messages": [{"role": "user", "content": f"Warmup stream {lane}. " + "context " * words +
                                          "\nWhat is 17 times 19? Reply with only the integer."}],
            "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
        })
        if choice.get("finish_reason") != "stop" or (choice.get("message", {}).get("content") or "").strip() != "323":
            raise RuntimeError(f"Incorrect warmup reply: {result!r}")
    with ThreadPoolExecutor(max_workers=lanes) as pool:
        list(pool.map(one, range(lanes)))


def tool_call(url, model):
    tool = {"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city.",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
    result, choice = chat(url, {
        "model": model, "messages": [{"role": "user", "content": "What is the weather in Oslo?"}],
        "tools": [tool], "tool_choice": "auto", "max_tokens": 256, "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    calls = choice.get("message", {}).get("tool_calls") or []
    if choice.get("finish_reason") != "tool_calls" or len(calls) != 1 or calls[0]["function"]["name"] != "get_weather":
        raise RuntimeError(f"Incorrect tool-call reply: {result!r}")
    if json.loads(calls[0]["function"]["arguments"]).get("city", "").lower() != "oslo":
        raise RuntimeError(f"Incorrect tool-call arguments: {result!r}")
    return calls


def render(url, model, effort):
    body = fetch(url + "/tokenize", {"model": model, "messages": PROMPT, "reasoning_effort": effort,
                                     "add_generation_prompt": True})
    tokens = body.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        raise RuntimeError(f"/tokenize returned no tokens for {effort!r}: {body!r}")
    return tuple(tokens)


def check_renders(url, model, efforts):
    """Each advertised level renders its own prompt; every other standard name renders as one of them."""
    try:
        rendered = {effort: render(url, model, effort) for effort in efforts}
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(set(rendered.values())) != len(rendered):
        raise RuntimeError(f"Advertised levels share a rendered prompt: {sorted(rendered)}")
    mapped = {}
    for effort in STANDARD_EFFORTS:
        if effort in rendered:
            continue
        tokens = render(url, model, effort)
        match = [name for name, value in rendered.items() if value == tokens]
        if len(match) != 1:
            raise RuntimeError(f"{effort!r} renders as none of the advertised levels {list(efforts)!r}")
        mapped[effort] = match[0]
    return mapped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", required=True)
    parser.add_argument("--context", type=int, required=True)
    parser.add_argument("--lanes", type=int, required=True)
    parser.add_argument("--efforts", required=True,
                        help="the model's own levels in the server's order, comma-separated; empty for none")
    parser.add_argument("--min-pool-tokens", type=int, default=0, help="shared-pool floor, Mia's Python server only")
    parser.add_argument("--warmup-words", default="", help="comma-separated prompt lengths to warm, in words")
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    efforts = [effort for effort in args.efforts.split(",") if effort]

    check_health(fetch(url + "/health"), args.model, args.lanes, args.context, args.min_pool_tokens)
    check_models(fetch(url + "/v1/models"), args.model, args.context, efforts)
    print("TensorFold discovery:", json.dumps({"model": args.model, "context": args.context, "efforts": efforts}),
          flush=True)
    for words in [int(w) for w in args.warmup_words.split(",") if w]:
        warmup(url, args.model, args.lanes, words)
        print("TensorFold warmup:", words, "words on", args.lanes, "streams", flush=True)
    with ThreadPoolExecutor(max_workers=args.lanes) as pool:
        results = list(pool.map(lambda lane: smoke(url, args.model, lane), range(args.lanes)))
    print("TensorFold smoke:", json.dumps(results), flush=True)
    print("TensorFold tool call:", json.dumps(tool_call(url, args.model)), flush=True)
    if efforts:
        mapped = check_renders(url, args.model, efforts)
        print("TensorFold levels:", "no /tokenize route" if mapped is None else json.dumps(mapped), flush=True)
    check_health(fetch(url + "/health"), args.model, args.lanes, args.context, args.min_pool_tokens)
    print("TensorFold readiness passed", flush=True)


if __name__ == "__main__":
    main()
