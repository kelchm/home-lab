#!/usr/bin/env python3
"""Site acceptance: five-image input and a short arrival during active decode.

Run alone against the deployed recipe. Performance sweeps belong to SparkRun;
this gate only checks the known multimodal and scheduler regression boundaries.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import random
import struct
import threading
import time
import urllib.request
import uuid
import zlib


def stream(url, model, content, limit, ready=None):
    body = dict(model=model, messages=[dict(role="user", content=content)],
                temperature=0, max_tokens=limit, stream=True,
                chat_template_kwargs={"enable_thinking": False})
    endpoint = url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"
    request = urllib.request.Request(endpoint,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    begin, first, finish, parts = time.monotonic(), None, None, []
    with urllib.request.urlopen(request, timeout=120) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            data = json.loads(payload)
            if data.get("error"):
                raise RuntimeError(data["error"])
            for choice in data.get("choices", []):
                finish = choice.get("finish_reason") or finish
                text = choice.get("delta", {}).get("content") or ""
                if text:
                    parts.append(text)
                    if first is None:
                        first = time.monotonic()
                        if ready is not None:
                            ready.set()
    if first is None or not finish:
        raise RuntimeError("Incomplete or empty response")
    return dict(text="".join(parts), first=first, end=time.monotonic(), ttft=first-begin)


def color_image(rgb):
    """Encode a solid RGB color as a 64-by-64 PNG for the multimodal request."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 64, 64, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgb) * 64) * 64)) + chunk(b"IEND", b""))
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def check_images(url, model):
    """Require all five distinct image colors in their randomized input order."""
    colors = [("red", (255, 0, 0)), ("green", (0, 128, 0)), ("blue", (0, 0, 255)),
              ("white", (255, 255, 255)), ("black", (0, 0, 0))]
    random.SystemRandom().shuffle(colors)
    image = stream(url, model, [
        {"type": "text", "text": "Name the solid color of each of the five images in image order. "
         "Reply with exactly five lowercase color words separated by commas, with no other text."},
        *[color_image(rgb) for _, rgb in colors],
    ], 32)
    expected = [name for name, _ in colors]
    if [word.strip().lower() for word in image["text"].split(",")] != expected:
        raise RuntimeError(f"Five-image response was incorrect: {image['text']!r}; expected {expected!r}")
    print("PASS: five-image input", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888",
                        help="Server root or OpenAI-compatible base URL ending in /v1")
    parser.add_argument("--model", default="GLM-5.3-Flash-EXL3")
    args = parser.parse_args()
    check_images(args.url, args.model)

    ready = threading.Event()
    nonce = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=1) as pool:
        ongoing = pool.submit(stream, args.url, args.model,
            nonce + " Explain database storage engine design in detail, with examples. Write at least 2000 words.",
            1024, ready)
        if not ready.wait(120):
            raise RuntimeError("Original decode did not produce content within 120 seconds")
        time.sleep(2)
        short = stream(args.url, args.model,
            nonce + "-new What is 17 times 19? Reply with only the integer.", 32)
        original = ongoing.result()
    if short["text"].strip() != "323" or short["ttft"] >= 5 or short["first"] >= original["end"]:
        raise RuntimeError(f"Short-arrival regression: answer={short['text']!r}, "
                           f"TTFT={short['ttft']:.3f}s, overlapped={short['first'] < original['end']}")
    print(f"PASS: short arrival during decode (TTFT {short['ttft']:.3f}s)")


if __name__ == "__main__":
    main()
