#!/usr/bin/env python3
"""Render DeepSeek's 1017 reference prompts through TensorFold's ChatTemplate, as the server drives it.

Run inside the serving image: dsv41_chat_template.py TEMPLATE FIXTURES, where FIXTURES is the weights snapshot's
encoding/tests. Image fixtures are skipped: the server replaces image parts before the template renders them.
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

from tensorfold.cuda.chat_template import ChatTemplate
from tensorfold.server.messages import _normalize_tool_call_arguments

EFFORT = {"minimal": 25, "low": 50, "medium": 62, "high": 75, "xhigh": 88, "max": 100}

template, fixtures = Path(sys.argv[1]), Path(sys.argv[2])
model = Path(tempfile.mkdtemp())
shutil.copy(template, model / "chat_template.jinja")
(model / "tokenizer_config.json").write_text(json.dumps({"bos_token": "<｜begin▁of▁sentence｜>", "eos_token": "<｜end▁of▁sentence｜>"}))
t = ChatTemplate(model)
failed = 0
for i in range(1, 6):
    data = json.loads((fixtures / f"test_input_{i}.json").read_text())
    case = data if isinstance(data, dict) else {"messages": data}
    messages = copy.deepcopy(case["messages"])
    if any(isinstance(m.get("content"), list) for m in messages):
        print(f"fixture {i}: skipped (image parts)")
        continue
    thinking = (case.get("thinking_mode") or "chat") == "thinking"
    extra = {"reasoning_effort": EFFORT.get(case.get("reasoning_effort") or "high", case.get("reasoning_effort"))} if thinking else {}
    if any(m["role"] == "latest_reminder" for m in messages):  # not an API role, so render the template directly
        got = t.template.render(**t.specials, messages=_normalize_tool_call_arguments(messages), tools=case.get("tools"),
                                add_generation_prompt=False, enable_thinking=thinking, **extra)
    else:
        got = t.render(messages, tools=case.get("tools"), enable_thinking=thinking, extra=extra)
    ok = got == (fixtures / f"test_output_{i}.txt").read_text()
    failed += not ok
    print(f"fixture {i}: {'match' if ok else 'DIFFERS'}")

call = {"id": "c1", "type": "function", "function": {"name": "c1", "arguments": "{}"}}
turn = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1", "reasoning_content": "r1"},
        {"role": "user", "content": "q2"}, {"role": "assistant", "content": "", "reasoning_content": "r2", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": "value"}]
prompt = t.render(turn, tools=None, enable_thinking=True, extra={"reasoning_effort": 75})
ok = "<think>r2</think>" in prompt and "r1" not in prompt
failed += not ok
print(f"reasoning before a tool call kept after its result: {'yes' if ok else 'NO'}")
sys.exit(1 if failed else 0)
