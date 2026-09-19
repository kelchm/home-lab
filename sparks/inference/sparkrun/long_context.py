#!/usr/bin/env python3
"""Exercise the configured context boundary with a synthetic retrieval request.

Tokenize with the serving tokenizer/template, then send those exact token IDs
through the completion endpoint. This is allocation/basic recall qualification,
not evidence of general reasoning quality at long context.
"""
import argparse
import json
from pathlib import Path
import urllib.request
import uuid
from mixed_workload import stream_request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://10.32.21.31:8888")
parser.add_argument("--tokens", type=int, default=850000, help="Total prompt + output budget")
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
if args.tokens < 4096:
    parser.error("Use at least 4096 tokens")
output_tokens = 32
prompt_tokens = args.tokens-output_tokens
# Deliberately overfill, then remove filler tokens while preserving the system,
# secret marker, final question, and assistant generation prefix.
text = (str(uuid.uuid4()) + " The secret marker is SILVERFOX. Remember it.\n" +
        "An orchard grows apples and pears.\n" * (prompt_tokens//6) +
        "\nWhat is the secret marker? Reply with only the marker.")
body = dict(model="GLM-5.3-Flash-EXL3", messages=[dict(role="user", content=text)],
            add_generation_prompt=True, chat_template_kwargs={"enable_thinking": False})
request = urllib.request.Request(args.url.rstrip("/")+"/tokenize", data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"})
with urllib.request.urlopen(request, timeout=180) as response:
    tokens = json.load(response)["tokens"]
excess = len(tokens)-prompt_tokens
if excess < 0:
    raise RuntimeError("Filler did not reach requested context")
if excess:
    del tokens[128:128+excess]
assert len(tokens)==prompt_tokens
print(f"Submitting {prompt_tokens} input tokens + {output_tokens} output budget", flush=True)
body = dict(model="GLM-5.3-Flash-EXL3", prompt=tokens, max_tokens=output_tokens,
            temperature=0, stream=True, stream_options={"include_usage":True})
result = stream_request(args.url.rstrip("/")+"/v1/completions", body)
result.update(requested_prompt_tokens=prompt_tokens, output_budget=output_tokens)
result["passed"] = (result["text"].strip()=="SILVERFOX" and
                    (result.get("usage") or {}).get("prompt_tokens")==prompt_tokens)
args.out.write_text(json.dumps(result,indent=2)+"\n")
print("PASS" if result["passed"] else "FAIL", round(result["ttft_s"],3), flush=True)
if not result["passed"]:
    raise SystemExit(1)
