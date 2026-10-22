#!/usr/bin/env python3
"""Fail-closed dsv41 TensorFold readiness gate for SparkRun: health, discovery, effort renders, concurrent smoke and a tool call."""
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


def check_models(body, model, context, efforts):
    entries = [m for m in body.get("data", []) if m.get("id") == model]
    if len(entries) != 1:
        raise RuntimeError(f"/v1/models does not list {model} once: {body!r}")
    entry = entries[0]
    if entry.get("owned_by") != "tensorfold":
        raise RuntimeError(f"/v1/models reports owned_by {entry.get('owned_by')!r}, not tensorfold")
    for field in ("context_length", "max_model_len"):
        if entry.get(field) != context:
            raise RuntimeError(f"/v1/models reports {field} {entry.get(field)!r}, not {context}")
    if entry.get("reasoning", {}).get("supported_efforts") != efforts:
        raise RuntimeError(f"/v1/models reports efforts {entry.get('reasoning', {}).get('supported_efforts')!r}, not {efforts}")


def check_renders(listed, others, aliases):
    """Each listed effort renders its own prompt, and each other effort name renders as the level it maps to."""
    if len({tuple(ids) for ids in listed.values()}) != len(listed):
        raise RuntimeError(f"Listed efforts share a rendered prompt: {sorted(listed)}")
    for alias, level in aliases.items():
        if others[alias] != listed[level]:
            raise RuntimeError(f"Effort {alias} does not render as {level}")


def chat(url, body):
    result = fetch(url + "/v1/chat/completions", body)
    if result.get("error"):
        raise RuntimeError(result["error"])
    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices: {result!r}")
    return result, choices[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888")
    parser.add_argument("--model", required=True)
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--context", type=int, required=True)
    parser.add_argument("--efforts", required=True, help="the listed efforts, comma-separated, in order")
    parser.add_argument("--aliases", default="", help="other effort names and the level each maps to: name=level,...")
    args = parser.parse_args()
    efforts = args.efforts.split(",")
    aliases = dict(pair.split("=") for pair in args.aliases.split(",") if pair)
    url = args.url.rstrip("/").removesuffix("/v1")
    before = fetch(url + "/health")
    check_health(before, args.model, args.lanes)
    check_models(fetch(url + "/v1/models"), args.model, args.context, efforts)
    print("TensorFold loaded health:", json.dumps(before, sort_keys=True), flush=True)

    def render(effort):
        return fetch(url + "/tokenize", {"model": args.model, "messages": [{"role": "user", "content": "Hi"}],
                                         "reasoning_effort": effort})["tokens"]

    renders = {effort: render(effort) for effort in efforts}
    check_renders(renders, {alias: render(alias) for alias in aliases}, aliases)
    print("TensorFold effort renders:", json.dumps({effort: len(ids) for effort, ids in renders.items()}), flush=True)

    def smoke(lane):
        started = time.monotonic()
        result, choice = chat(url, {
            "model": args.model, "messages": [{"role": "user", "content":
                f"Smoke stream {lane}. What is 17 times 19? Reply with only the integer."}],
            "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
        })
        if choice.get("finish_reason") != "stop" or choice.get("message", {}).get("content", "").strip() != "323":
            raise RuntimeError(f"Incorrect smoke reply: {result!r}")
        usage = result.get("usage", {})
        if not usage.get("prompt_tokens") or not usage.get("completion_tokens"):
            raise RuntimeError(f"Missing smoke usage: {result!r}")
        return {"stream": lane, "seconds": round(time.monotonic() - started, 3), "usage": usage}

    with ThreadPoolExecutor(max_workers=args.lanes) as pool:
        results = list(pool.map(smoke, range(args.lanes)))
    print("TensorFold smoke:", json.dumps(results), flush=True)

    tool = {"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city.",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
    result, choice = chat(url, {
        "model": args.model, "messages": [{"role": "user", "content": "What is the weather in Oslo?"}],
        "tools": [tool], "tool_choice": "auto", "max_tokens": 256, "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    calls = choice.get("message", {}).get("tool_calls") or []
    if choice.get("finish_reason") != "tool_calls" or len(calls) != 1 or calls[0]["function"]["name"] != "get_weather":
        raise RuntimeError(f"Incorrect tool-call reply: {result!r}")
    if json.loads(calls[0]["function"]["arguments"]).get("city", "").lower() != "oslo":
        raise RuntimeError(f"Incorrect tool-call arguments: {result!r}")
    print("TensorFold tool call:", json.dumps(calls), flush=True)

    check_health(fetch(url + "/health"), args.model, args.lanes)
    print("TensorFold readiness, concurrent smoke test and tool call passed", flush=True)


if __name__ == "__main__":
    main()
