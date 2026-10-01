#!/usr/bin/env python3
"""Check the installed TensorFold tool-history code without loading model weights.

Run inside the candidate image. Two parallel conversations deliberately share
their last user message and tool arguments, while keeping different histories.
"""
from copy import deepcopy

from tensorfold.families.glm5_next.cuda.toolfix import ReasoningMemory, ToolFixes


def call(call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}


def history(label):
    return [{"role": "system", "content": f"Public test project {label}."},
            {"role": "user", "content": "Read README.md and report the project label."}]


def remember(fixes, label, call_id):
    fixes.reply({"messages": history(label)}, content="", calls=[call(call_id)],
                reasoning=f"Use the public metadata for project {label}.",
                last=None, thinking=True, stopped=False)


def resume(fixes, label, call_id, **fields):
    assistant = {"role": "assistant", "content": None, "tool_calls": [call(call_id)], **fields}
    body = {"messages": history(label) + [assistant,
            {"role": "tool", "tool_call_id": call_id, "content": "Public README fixture."}]}
    assert fixes.prepare(body) is None
    first = deepcopy(body)
    assert fixes.prepare(body) is None and body == first, "Preparation must remain idempotent"
    return assistant


def main():
    fixes = ToolFixes(None, raw="history,reasoning")
    a, b = "call_" + "1" * 24, "call_" + "2" * 24
    remember(fixes, "A", a)
    remember(fixes, "B", b)
    for label, call_id in (("A", a), ("B", b)):
        restored = resume(fixes, label, call_id)
        assert restored["reasoning_content"] == f"Use the public metadata for project {label}.", restored
        assert restored["content"] == ""
    for missing_id in ("client-renumbered-A", "call_" + "3" * 24, None):
        restored = resume(fixes, "A", missing_id)
        assert "reasoning_content" not in restored, f"Guessed another tool turn's reasoning: {restored}"
    for field in ("reasoning", "reasoning_content"):
        supplied = resume(fixes, "A", "client-renumbered-A", **{field: "Caller retained this reasoning."})
        assert supplied["reasoning_content"] == "Caller retained this reasoning.", supplied
    fixes.memory = ReasoningMemory(entries=1)
    remember(fixes, "A", a)
    remember(fixes, "B", b)
    assert "reasoning_content" not in resume(fixes, "A", a), "An evicted turn must stay missing"
    assert resume(fixes, "B", b)["reasoning_content"] == "Use the public metadata for project B."
    print("PASS exact tool-turn reasoning, parallel histories, renamed/missing IDs, caller reasoning and eviction")


if __name__ == "__main__":
    main()
