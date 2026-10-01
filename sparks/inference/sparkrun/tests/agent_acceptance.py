#!/usr/bin/env python3
"""Live agent/API qualification shared by the SparkRun GLM candidates."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request

TOOL = {"type": "function", "function": {"name": "multiply", "description": "Multiply two integers.",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                       "required": ["a", "b"], "additionalProperties": False}}}


class Qualification:
    def __init__(self, args):
        self.args = args
        path = Path(args.output)
        self.rows = json.loads(path.read_text()) if args.section != "all" and path.exists() else []
        self.lock = threading.Lock()

    def request(self, messages, stream=False, **extra):
        body = {"model": self.args.model, "messages": messages, "temperature": 0, "max_tokens": 1024,
                "chat_template_kwargs": {"enable_thinking": False}, "stream": stream, **extra}
        if stream:
            body["stream_options"] = {"include_usage": True}
        request = urllib.request.Request(self.args.url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=240) as response:
            if not stream:
                result = json.load(response)
            else:
                message, calls, usage, finish, done = {"content": "", "reasoning_content": ""}, {}, None, None, False
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        done = True
                        break
                    data = json.loads(payload)
                    if data.get("error"):
                        raise RuntimeError(data["error"])
                    usage = data.get("usage") or usage
                    for choice in data.get("choices", []):
                        finish = choice.get("finish_reason") or finish
                        delta = choice.get("delta") or {}
                        message["content"] += delta.get("content") or ""
                        message["reasoning_content"] += delta.get("reasoning_content") or delta.get("reasoning") or ""
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(part["index"], {"id": "", "type": "function",
                                                                  "function": {"name": "", "arguments": ""}})
                            call["id"] += part.get("id") or ""
                            function = part.get("function") or {}
                            call["function"]["name"] += function.get("name") or ""
                            call["function"]["arguments"] += function.get("arguments") or ""
                if not done or not finish or usage is None:
                    raise RuntimeError(f"Incomplete stream: done={done}, finish={finish}, usage={usage}")
                if calls:
                    message["tool_calls"] = [calls[index] for index in sorted(calls)]
                result = {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}
        if result.get("error"):
            raise RuntimeError(result["error"])
        if not result.get("choices") or not result["choices"][0].get("finish_reason"):
            raise RuntimeError(f"Incomplete API response: {result!r}")
        return result, time.monotonic() - started

    def record(self, name, result, elapsed, error=None):
        with self.lock:
            self.rows = [row for row in self.rows if row["case"] != name]
            self.rows.append({"case": name, "elapsed_s": elapsed, "result": result,
                              "status": "fail" if error else "pass", "error": error})
            Path(self.args.output).write_text(json.dumps(self.rows, indent=2) + "\n")
            print(f"{'FAIL' if error else 'PASS'} {name} ({elapsed:.3f}s)" + (f": {error}" if error else ""), flush=True)

    def tools(self):
        messages = [{"role": "user", "content": "Use multiply to calculate 17 times 19. Do not answer directly."}]
        for streamed in (False, True):
            result, elapsed = self.request(messages, streamed, tools=[TOOL], tool_choice={
                "type": "function", "function": {"name": "multiply"}})
            calls = result["choices"][0]["message"].get("tool_calls") or []
            assert len(calls) == 1, result
            if result["choices"][0]["finish_reason"] != "tool_calls":
                print("LIMITATION: named tool choice finished with " + result["choices"][0]["finish_reason"], flush=True)
            assert calls[0]["function"]["name"] == "multiply" and calls[0]["id"], result
            arguments = json.loads(calls[0]["function"]["arguments"])
            assert arguments == {"a": 17, "b": 19} and all(type(value) is int for value in arguments.values()), result
            self.record(f"forced-tool-{'stream' if streamed else 'nonstream'}", result, elapsed)
            # Exercise the null-content assistant form emitted by coding clients.
            history = messages + [{"role": "assistant", "content": None, "tool_calls": calls},
                                  {"role": "tool", "tool_call_id": calls[0]["id"], "content": "323"},
                                  {"role": "user", "content": "Reply with only the result integer."}]
            final, elapsed = self.request(history, streamed, tools=[TOOL], tool_choice="none", max_tokens=64)
            assert final["choices"][0]["message"]["content"].strip() == "323", final
            self.record(f"null-content-tool-continuation-{'stream' if streamed else 'nonstream'}", final, elapsed)
        result, elapsed = self.request([{"role": "user", "content": "What is 17 times 19? Reply with only the integer."}],
                                       tools=[TOOL], tool_choice="none", max_tokens=64)
        assert not result["choices"][0]["message"].get("tool_calls"), result
        assert "323" in result["choices"][0]["message"]["content"], result
        self.record("tool-choice-none", result, elapsed)

    def isolation(self):
        def initial(secret):
            history = [{"role": "system", "content": f"Private session marker: {secret}. Remember it."},
                       {"role": "user", "content": "Use multiply with factors x and y. First solve 3*x+7=58 and 4*y-5=71. "
                        "Work out both factors carefully before calling the tool; do not multiply them yourself. "
                        "After the tool returns, answer with the private marker, a colon, and the result. "
                        "Never mention any other session marker."}]
            result, elapsed = self.request(history, True, tools=[TOOL], tool_choice="auto",
                                            reasoning_effort="high", chat_template_kwargs={
                                                "enable_thinking": True, "reasoning_effort": "high"})
            message = result["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            assert len(calls) == 1 and json.loads(calls[0]["function"]["arguments"]) == {"a": 17, "b": 19}, result
            assert message.get("reasoning_content") or message.get("reasoning"), result
            self.record("high-thinking-tool-" + secret, result, elapsed)
            # Deliberately omit reasoning while preserving the server call IDs.
            # Renumbered-ID cache isolation is checked against installed code by
            # tensorfold_tool_history.py; output alone cannot prove that lookup.
            return secret, history + [{"role": "assistant", "content": None, "tool_calls": calls},
                                     {"role": "tool", "tool_call_id": calls[0]["id"], "content": "323"},
                                     {"role": "user", "content": "Reply with exactly marker:result and no extra text."}]

        with ThreadPoolExecutor(max_workers=2) as pool:
            histories = list(pool.map(initial, ("amber-913", "violet-827")))
        # Resume in reverse order to catch a last-writer-wins tool-history cache.
        for secret, history in reversed(histories):
            result, elapsed = self.request(history, True, tools=[TOOL], tool_choice="none", max_tokens=64)
            assert result["choices"][0]["message"]["content"].strip() == secret + ":323", result
            self.record("interleaved-history-" + secret, result, elapsed)

    def structured(self):
        schema = {"type": "object", "properties": {"answer": {"type": "integer"}, "ok": {"type": "boolean"}},
                  "required": ["answer", "ok"], "additionalProperties": False}
        for stream in (False, True):
            result, elapsed = self.request([{"role": "user", "content": "What is 17 times 19? Return answer and ok=true."}],
                stream, max_tokens=64, response_format={"type": "json_schema", "json_schema": {
                    "name": "arithmetic", "strict": True, "schema": schema}})
            assert json.loads(result["choices"][0]["message"]["content"]) == {"answer": 323, "ok": True}, result
            self.record("structured-" + str(stream), result, elapsed)

    def cache(self):
        messages = [{"role": "user", "content": "Cache-accounting qualification. " + "context " * 8192 +
                     "\nWhat is 17 times 19? Reply with only the integer."}]
        for stream in (False, True):
            self.request(messages, stream, max_tokens=64)
            result, elapsed = self.request(messages, stream, max_tokens=64)
            usage = result.get("usage", {})
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            assert result["choices"][0]["message"]["content"].strip() == "323", result
            if self.args.require_cache:
                assert isinstance(cached, int) and 0 < cached <= usage["prompt_tokens"], result
            self.record("cache-accounting-" + str(stream), result, elapsed)

    def coding(self):
        tasks = [
            ("merge_intervals", "Return sorted merged closed intervals as a list of two-element lists. Merge intervals that overlap or touch. "
             "Input is a list of two-element lists; do not mutate it. Empty input returns [].",
             [[[[5, 7], [1, 3], [3, 6]]], [[[1, 2], [4, 5]]], [[]], [[[2, 3], [1, 8], [8, 9]]]],
             [[[1, 7]], [[1, 2], [4, 5]], [], [[1, 9]]]),
            ("rotate_grid", "Return a rectangular grid rotated 90 degrees clockwise as a list of lists. "
             "Input is a list of lists. Empty input returns []. Do not mutate it.",
             [[[[1, 2, 3], [4, 5, 6]]], [[[1], [2], [3]]], [[]], [[[9]]]],
             [[[4, 1], [5, 2], [6, 3]], [[3, 2, 1]], [], [[9]]]),
            ("stable_topological_order", "Accept (nodes, edges), where nodes are unique strings and edges are pairs "
             "[source, destination]. Return the lexicographically smallest valid topological ordering. "
             "Ignore duplicate edges. Raise ValueError on a cycle. Do not mutate inputs.",
             [[['c', 'b', 'a'], [['a', 'c']]], [['z', 'a', 'b'], []], [['a', 'b'], [['a', 'b'], ['a', 'b']]],
              [['a', 'b'], [['a', 'b'], ['b', 'a']]]],
             [['a', 'b', 'c'], ['a', 'b', 'z'], ['a', 'b'], {"raises": "ValueError"}]),
        ]
        failures = []
        for temperature in (0, 0.6):
            for name, description, inputs, expected in tasks:
                result, elapsed = self.request([{"role": "user", "content":
                    f"Write Python function {name}. {description} Return only Python code. Use no imports."}],
                    temperature=temperature, seed=20260930, max_tokens=1024)
                text = result["choices"][0]["message"]["content"].strip()
                match = re.search(r"```(?:python)?\s*\n(.*?)\n```", text, re.S)
                code = match.group(1) if match else text
                if match and text[match.end():].strip():
                    print(f"LIMITATION: {name} returned text after its code block", flush=True)
                try:
                    execute(code, name, inputs, expected)
                except Exception as error:
                    Path(self.args.output).with_suffix(".failure.json").write_text(json.dumps({
                        "case": name, "temperature": temperature, "error": str(error), "result": result}, indent=2))
                    failures.append(f"{name} t={temperature}: {error}")
                    self.record(f"coding-{name}-t{temperature}", result, elapsed, str(error))
                else:
                    self.record(f"coding-{name}-t{temperature}", result, elapsed)
        if failures:
            raise RuntimeError("Coding qualification failed: " + "; ".join(failures))

    def cancellation(self):
        body = {"model": self.args.model, "messages": [{"role": "user", "content":
            "Write 3000 words explaining database indexes with many examples."}], "max_tokens": 2048,
            "stream": True, "chat_template_kwargs": {"enable_thinking": False}}
        request = urllib.request.Request(self.args.url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            received = 0
            for line in response:
                if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                    data = json.loads(line[5:])
                    if any(choice.get("delta", {}).get("content") for choice in data.get("choices", [])):
                        received += 1
                    if received >= 5:
                        break
            assert received >= 5
        time.sleep(1)
        result, elapsed = self.request([{"role": "user", "content": "What is 17 times 19? Reply only with the integer."}],
                                        max_tokens=32)
        assert result["choices"][0]["message"]["content"].strip() == "323" and elapsed < 5, result
        self.record("cancel-and-continue", result, elapsed)

    def run(self):
        for name in ("tools", "isolation", "structured", "cache", "coding", "cancellation"):
            if self.args.section in ("all", name):
                getattr(self, name)()


def execute(code, name, inputs, expected):
    """Execute only pure Python under bounded resources and restricted builtins."""
    tree = ast.parse(code)
    builtins = {"len", "sorted", "range", "min", "max", "set", "str", "int", "sum", "list", "tuple", "dict",
                "zip", "enumerate", "reversed", "bool", "ValueError"}
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    methods = {"get", "append", "insert", "items", "keys", "values", "sort", "pop", "add", "remove", "discard", "extend", "copy"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.With, ast.AsyncWith,
                             ast.Global, ast.Nonlocal, ast.Yield, ast.Await)):
            raise ValueError(f"Unsupported generated code: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("Generated dunder name")
        if isinstance(node, ast.Attribute) and node.attr not in methods:
            raise ValueError(f"Unsupported generated method: {node.attr}")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError("Generated decorator")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in builtins | functions or
            isinstance(node.func, ast.Attribute) and node.func.attr in methods):
            raise ValueError("Unsupported generated call")
    child = """
import builtins, copy, json, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (256*1024**2, 256*1024**2))
body = json.load(sys.stdin)
scope = {'__builtins__': {name: getattr(builtins, name) for name in body['builtins']}}
exec(compile(body['code'], '<generated>', 'exec'), scope)
function = scope[body['name']]
for args, expected in zip(body['inputs'], body['expected']):
    original = copy.deepcopy(args)
    try:
        actual = function(*args)
    except ValueError:
        assert expected == {'raises': 'ValueError'}
    else:
        assert actual == expected, (args, actual, expected)
    assert args == original, ('input mutated', args, original)
print('PASS')
"""
    result = subprocess.run(["python3", "-I", "-c", child], input=json.dumps({"code": code, "name": name,
        "inputs": inputs, "expected": expected, "builtins": sorted(builtins)}), text=True, capture_output=True, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"Generated code failed: {result.stderr}\n{code}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8888/v1")
    parser.add_argument("--model", default="GLM-5.3-Flash-EXL3")
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-cache", action="store_true")
    parser.add_argument("--section", choices=("all", "tools", "isolation", "structured", "cache", "coding", "cancellation"), default="all")
    Qualification(parser.parse_args()).run()


if __name__ == "__main__":
    main()
