#!/usr/bin/env python3
"""Prime the shared text of a batch of GLM chat requests on the TensorFold endpoint that will serve them."""

import argparse
import http.client
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_UNSET = object()


def post(url, path, body):
    request = Request(url.rstrip("/") + path, data=json.dumps(body, separators=(",", ":")).encode(),
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=600) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(512).decode("utf-8", errors="replace").strip()
        except OSError:
            pass
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"POST {path} failed with HTTP {exc.code}{suffix}") from exc
    except (URLError, TimeoutError, http.client.HTTPException, OSError) as exc:
        raise RuntimeError(f"POST {path} failed: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"POST {path} returned invalid JSON: {exc}") from exc


def chat_body(path):
    body = json.loads(path.read_bytes())
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        raise ValueError(f"{path}: expected a chat request naming the served model")
    if "cache_salt" in body and body["cache_salt"] is not None:
        salt = body["cache_salt"]
        if not isinstance(salt, str) or not salt:
            raise ValueError(f"{path}: cache_salt must be a non-empty string")
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
    cache_salt = _UNSET
    prefix = None
    for path in paths:
        body = chat_body(path)
        if model is None:
            model = body["model"]
        elif model != body["model"]:
            raise ValueError("All requests must use the same served model")
        salt = body.get("cache_salt")
        if cache_salt is _UNSET:
            cache_salt = salt
        elif salt != cache_salt:
            raise ValueError("All requests must use the same cache_salt")
        rendered = post(url, "/tokenize", body)
        if not isinstance(rendered, dict):
            raise ValueError("Expected a JSON object from /tokenize")
        ids = rendered.get("tokens")
        count = rendered.get("count")
        if (
            not isinstance(ids, list)
            or type(count) is not int
            or len(ids) != count
            or not all(type(i) is int and i >= 0 for i in ids)
        ):
            raise ValueError("Expected integer token IDs and their count from /tokenize")
        if prefix is None:
            prefix = ids
        else:
            common = next((i for i, (a, b) in enumerate(zip(prefix, ids)) if a != b), min(len(prefix), len(ids)))
            del prefix[common:]

    if len(prefix) < minimum:
        return {"shared_tokens": len(prefix), "primed": False}
    payload = {"model": model, "prompt": prefix, "temperature": 0, "max_tokens": 1}
    if cache_salt is not None:
        payload["cache_salt"] = cache_salt
    response = post(url, "/v1/completions", payload)
    if not isinstance(response, dict):
        raise ValueError("Expected a JSON object from /v1/completions")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Expected a usage object from /v1/completions")
    prompt_tokens = usage.get("prompt_tokens")
    if type(prompt_tokens) is not int:
        raise ValueError("Expected integer prompt_tokens in /v1/completions usage")
    if prompt_tokens != len(prefix):
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
