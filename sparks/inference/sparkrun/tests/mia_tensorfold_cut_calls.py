#!/usr/bin/env python3
"""Check v1.3's client-visible output-limit behavior on the live native instance."""
import argparse
from pathlib import Path

from tokenizers import Tokenizer

from agent_acceptance import Qualification


TOOLS = [{"type": "function", "function": {"name": "write_payload",
    "description": "Write the exact supplied public fixture text.", "parameters": {
        "type": "object", "properties": {"text": {"type": "string"}},
        "required": ["text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "mark_done", "parameters": {
        "type": "object", "properties": {}, "additionalProperties": False}}}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888/v1")
    parser.add_argument("--model", default="GLM-5.3-Flash-EXL3")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    args = parser.parse_args()
    args.section = "all"
    probe = Qualification(args)
    tokenizer = Tokenizer.from_file(str(args.tokenizer))
    payload = "xq49" * 1024
    messages = [{"role": "user", "content":
        "Call write_payload with exactly the text below as its text argument. "
        "Copy every character literally; do not abbreviate, summarize, answer directly or call mark_done.\n" + payload}]
    failed = False
    for streamed in (False, True):
        name = "cut-tool-call-" + ("stream" if streamed else "nonstream")
        result, elapsed = None, 0
        try:
            result, elapsed = probe.request(messages, streamed, tools=TOOLS, tool_choice="auto",
                                             parallel_tool_calls=True, max_tokens=16, seed=20260930,
                                             return_token_ids=True)
            tokens = result.get("tensorfold", {}).get("token_ids")
            assert isinstance(tokens, list) and len(tokens) == 16, "Missing raw generation evidence"
            raw = tokenizer.decode(tokens, skip_special_tokens=False)
            result["raw_generated_text"] = raw
            assert "<tool_call>" in raw and "</tool_call>" not in raw, "Probe did not cut an actual tool call"
            choice = result["choices"][0]
            assert choice["finish_reason"] == "length", "Probe did not reach its output limit"
            assert result["usage"]["completion_tokens"] == 16, "Probe did not generate its full small budget"
            assert not choice["message"].get("tool_calls"), "Cut tool arguments reached the client"
            probe.record(name, result, elapsed)
        except Exception as error:
            failed = True
            probe.record(name, result, elapsed, f"{type(error).__name__}: {error}")
    if failed:
        raise SystemExit(1)
    print("PASS live streamed and nonstreamed output-limit replies omit incomplete tool calls")


if __name__ == "__main__":
    main()
