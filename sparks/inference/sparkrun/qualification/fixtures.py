"""Small, deterministic fixtures. Token allocations are requirements, not measurements."""
import hashlib
import json
import struct
import zlib


VERSION = "glm-four-agent-v1"
CONTRACT = {
    "streams": 4,
    "total_window_tokens": 1048576,
    "output_budget_tokens": 32768,
    "aggregate_live_pool_floor_tokens": 2582528,
    "image_tokens": 2048,
    "max_images": 50,
    "request_image_tokens": 102400,
    "max_videos": 4,
    "video_tokens": 16384,
    "video_frames": 128,
    "request_video_tokens": 32768,
    "durable_gib_per_rank_floor": 64,
}
POLICY = {"primary_metric": "four_agent_tool_completion_wall_s",
          "max_wall_regression_fraction": 0.0, "required_completed_tasks": 4,
          "correctness_failures_allowed": 0}
TOOL = {"type": "function", "function": {
    "name": "multiply", "description": "Multiply two integers from the session ledger.",
    "parameters": {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False}}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha256(canonical(value))


def image(seed, index):
    """Color swatch with a unique seeded binary footer; stable PNG without dependencies."""
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
    color = int(sha256(f"{seed}/image/{index}".encode())[:8], 16) % len(colors)

    def chunk(name, data):
        return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data))

    # Stored DEFLATE blocks avoid compressor-version-dependent fixture hashes.
    bits = bytes.fromhex(sha256(f"{seed}/footer/{index}".encode()))
    footer = b"".join(bytes([255 if (bits[x // 8] >> (x % 8)) & 1 else 0]) * 3 for x in range(32))
    raw = (b"\x00" + bytes(colors[color]) * 32) * 28 + (b"\x00" + footer) * 4
    compressed = (b"\x78\x01\x01" + struct.pack("<HH", len(raw), 65535 - len(raw))
                  + raw + struct.pack("!I", zlib.adler32(raw)))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
    return png, ("red", "green", "blue", "yellow")[color]


def fixtures(seed=20261106):
    sessions = []
    for index in range(4):
        salt = sha256(f"{seed}/session/{index}".encode())
        a, b = 11 + int(salt[:4], 16) % 79, 11 + int(salt[4:8], 16) % 79
        marker = f"agent-{index}-{salt[8:20]}"
        ledger = "\n".join(f"record_{n:03d}={sha256(f'{seed}/{index}/{n}'.encode())[:24]}"
                           for n in range(64))
        messages = [{"role": "system", "content":
                     f"This session's marker is {marker}. Preserve it across turns.\n{ledger}"},
                    {"role": "user", "content":
                     f"Ledger factors: a={a}, b={b}. Reason about these factors, then call multiply "
                     "exactly once with these integers. Do not compute the product yourself. "
                     "After its receipt, reply with exactly marker:product."}]
        sessions.append({"id": index, "marker": marker, "a": a, "b": b,
                         "messages": messages, "text_sha256": digest(messages),
                         "tool_input_sha256": digest({"name": "multiply", "arguments": {"a": a, "b": b}}),
                         "tool_result_sha256": sha256(str(a * b).encode()), "answer": f"{marker}:{a * b}"})
    images = []
    for index in range(50):
        png, color = image(seed, index)
        images.append({"index": index, "file": f"image-{index:02d}.png", "sha256": sha256(png),
                       "expected_color": color, "token_cap": 2048})
    bundle = {"version": VERSION, "seed": seed, "sessions": sessions,
              "tool": TOOL, "tool_sha256": digest(TOOL), "images": images, "contract": CONTRACT}
    return bundle


def write_fixtures(directory, seed):
    directory.mkdir(parents=True, exist_ok=False)
    bundle = fixtures(seed)
    for entry in bundle["images"]:
        (directory / entry["file"]).write_bytes(image(seed, entry["index"])[0])
    (directory / "fixtures.json").write_bytes(canonical(bundle) + b"\n")
    return {"fixture_sha256": digest(bundle), "directory": str(directory)}


def body(model, seed, messages, turn):
    return {"model": model, "messages": messages, "tools": [TOOL],
            "tool_choice": {"type": "function", "function": {"name": "multiply"}} if turn == 0 else "none",
            "temperature": 0, "seed": seed, "max_tokens": 1024, "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": True}}
