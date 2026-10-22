#!/usr/bin/env python3
"""Fail-closed readiness gate for SparkRun: health, lanes, discovery, a smoke reply on every lane and a tool call."""
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


def check_health(body, model, lanes, min_lanes):
    """The loaded lane count: exactly `lanes` when given, else at least `min_lanes`."""
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise RuntimeError(f"TensorFold engine is unhealthy: {body!r}")
    if body.get("warming") is not False:
        raise RuntimeError(f"TensorFold engine is still warming: {body!r}")
    if body.get("model") != model:
        raise RuntimeError(f"Expected model {model}, got {body.get('model')!r}")
    loaded = body.get("max_batch_size")
    if not isinstance(loaded, int) or (loaded != lanes if lanes else loaded < min_lanes):
        raise RuntimeError(f"Expected {lanes or f'at least {min_lanes}'} loaded lanes, got {loaded!r}")
    return loaded


def check_models(body, model, context, efforts):
    """The discovery entry the contract fixes: one entry, TensorFold's owner, the window twice, the efforts in order."""
    entries = [m for m in body.get("data", []) if m.get("id") == model]
    if len(entries) != 1:
        raise RuntimeError(f"/v1/models does not list {model} once: {body!r}")
    entry = entries[0]
    reported = {"owned_by": entry.get("owned_by"), "context_length": entry.get("context_length"),
                "max_model_len": entry.get("max_model_len"),
                "supported_efforts": (entry.get("reasoning") or {}).get("supported_efforts")}
    expected = {"owned_by": "tensorfold", "context_length": context, "max_model_len": context,
                "supported_efforts": efforts}
    if reported != expected:
        raise RuntimeError(f"/v1/models reports {reported!r}, not {expected!r}")


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
    parser.add_argument("--context", type=int, required=True)
    parser.add_argument("--efforts", required=True, help="the reasoning efforts /v1/models must list, in order")
    parser.add_argument("--lanes", type=int, help="the exact lane count, for a fixed --parallel")
    parser.add_argument("--min-lanes", type=int, default=1, help="the fewest lanes --parallel auto may load")
    args = parser.parse_args()
    url = args.url.rstrip("/").removesuffix("/v1")
    before = fetch(url + "/health")
    lanes = check_health(before, args.model, args.lanes, args.min_lanes)
    check_models(fetch(url + "/v1/models"), args.model, args.context, args.efforts.split(","))
    print("TensorFold loaded health:", json.dumps(before, sort_keys=True), flush=True)

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

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        results = list(pool.map(smoke, range(lanes)))
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

    check_health(fetch(url + "/health"), args.model, lanes, lanes)
    print(f"TensorFold readiness on {lanes} lanes, concurrent smoke test and tool call passed", flush=True)


if __name__ == "__main__":
    main()
