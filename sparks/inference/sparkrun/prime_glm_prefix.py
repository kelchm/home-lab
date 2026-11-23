#!/usr/bin/env python3
"""Prime the shared text of a batch of GLM chat requests on the TensorFold endpoint that will serve them."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def post(url, path, body):
    request = Request(url.rstrip("/") + path, data=json.dumps(body, separators=(",", ":")).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=600) as response:
        return json.load(response)


def chat_body(path):
    body = json.loads(path.read_bytes())
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        raise ValueError(f"{path}: expected a chat request naming the served model")
    messages = body.get("messages")
    if "prompt" in body or not isinstance(messages, list) or not messages:
        raise ValueError(f"{path}: expected chat messages")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError(f"{path}: expected a message object")
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if not isinstance(content, list) or not all(
            isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            for part in content
        ):
            raise ValueError(f"{path}: priming supports text-only chat requests")
    return body


def prime(url, paths, minimum):
    if len(paths) < 2 or minimum < 1:
        raise ValueError("Supply at least two request files and a positive minimum prefix length")
    model = None
    prefix = None
    for path in paths:
        body = chat_body(path)
        if model is None:
            model = body["model"]
        elif model != body["model"]:
            raise ValueError("All requests must use the same served model")
        rendered = post(url, "/tokenize", body)
        ids = rendered.get("tokens")
        if not isinstance(ids, list) or len(ids) != rendered.get("count") or not all(type(i) is int and i >= 0 for i in ids):
            raise ValueError("Expected integer token IDs and their count from /tokenize")
        if prefix is None:
            prefix = ids
        else:
            common = next((i for i, (a, b) in enumerate(zip(prefix, ids)) if a != b), min(len(prefix), len(ids)))
            del prefix[common:]

    if len(prefix) < minimum:
        return {"shared_tokens": len(prefix), "primed": False}
    response = post(url, "/v1/completions", {"model": model, "prompt": prefix, "temperature": 0, "max_tokens": 1})
    usage = response["usage"]
    if usage["prompt_tokens"] != len(prefix):
        raise ValueError("Completion prompt count differs from the supplied token prefix")
    return {"shared_tokens": len(prefix), "primed": True, "usage": usage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="server root, for example http://10.32.21.31:8888")
    parser.add_argument("--min-tokens", type=int, default=2048, help="skip priming below this shared prefix length")
    parser.add_argument("requests", nargs="+", type=Path, help="two or more representative chat request JSON files")
    args = parser.parse_args()
    print(json.dumps(prime(args.url, args.requests, args.min_tokens)))


if __name__ == "__main__":
    main()
