#!/usr/bin/env python3
"""Guard exact tool turns, expose failed concurrent engines to SparkRun and report the window and levels in /v1/models."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Mia TensorFold source anchor drifted: {old[:80]!r}")
    return text.replace(old, new, 1)


def reasoning(text):
    text = replace_once(text, '    return _ids(calls) + [signature(history, calls)]\n', '    return _ids(calls)\n')
    return replace_once(text,
        'ids). Here the signature covers the whole conversation before the calls, not only the last user message, so a reply\n'
        'is only ever put back into the conversation it was written for.',
        'ids). Site guard: only original server call ids are used. A history signature cannot distinguish two\n'
        'parallel replies to identical messages and calls; clients that renumber ids must retain their own reasoning.')


def health(text):
    return replace_once(text, '        return body\n',
        '        body["mode"] = "strict"\n'
        '        thread = getattr(scheduler, "thread", None)\n'
        '        fatal = getattr(decoder, "broken", None)\n'
        '        body["ok"] = bool(decoder is not None and thread is not None and thread.is_alive() and fatal is None)\n'
        '        if fatal is not None:\n'
        '            body["fatal"] = f"{type(fatal).__name__}: {fatal}"\n'
        '        elif not body["ok"]:\n'
        '            body["fatal"] = "Concurrent scheduler unavailable"\n'
        '        return body\n')


DISCOVERY = '''

_EFFORT_ORDER = ("max", "xhigh", "high", "medium", "low", "minimal")


def _model_entry(app, model_id):
    """A /v1/models entry with the served window and the template's own reasoning levels, highest first."""
    entry = {"id": model_id, "object": "model", "owned_by": "tensorfold"}
    window = getattr(app, "effective_context_window", None)
    if window:
        entry["context_length"] = entry["max_model_len"] = int(window)
    named = getattr(getattr(app, "template", None), "efforts", ())
    levels = [name for name in _EFFORT_ORDER if name in named]
    if levels:   # reasoning_effort "none" and enable_thinking false both turn thinking off
        entry["reasoning"] = {"default_enabled": bool(app.default_thinking), "supported_efforts": levels + ["none"]}
    return entry
'''


def http(text):
    text = replace_once(text, '_TOKENIZER_ROUTES = ("/tokenize", "/v1/tokenize", "/detokenize", "/v1/detokenize")\n',
        '_TOKENIZER_ROUTES = ("/tokenize", "/v1/tokenize", "/detokenize", "/v1/detokenize")\n' + DISCOVERY)
    text = replace_once(text,
        '                self._json(200, {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "tensorfold"}\n'
        '                                                            for model_id in app.model_ids]})',
        '                self._json(200, {"object": "list", "data": [_model_entry(app, model_id) for model_id in app.model_ids]})')
    return replace_once(text, '                self._json(200, health.of(app).snapshot(app))',
        '                report = health.of(app).snapshot(app)\n'
        '                self._json(200 if report.get("ok") is True else 503, report)')


def apply(root):
    lock = json.loads(Path(__file__).with_name("upstream.lock.json").read_text())
    transforms = {"families/glm5_next/cuda/kept_reasoning.py": reasoning,
                  "cuda/health.py": health, "cuda/http.py": http}
    pending = []
    for name, transform in transforms.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or linked installed source: {name}")
        data = path.read_bytes()
        pins = lock["installed_files"][name]
        digest = hashlib.sha256(data).hexdigest()
        if digest == pins["site_sha256"]:
            continue
        if digest != pins["upstream_sha256"]:
            raise ValueError(f"Mia TensorFold installed source drifted: {name}")
        result = transform(data.decode()).encode()
        if hashlib.sha256(result).hexdigest() != pins["site_sha256"]:
            raise ValueError(f"Site transform changed without a reviewed pin: {name}")
        compile(result, str(path), "exec")
        pending.append((path, result))
    for path, data in pending:
        path.write_bytes(data)
    print(f"Verified Mia TensorFold site guards ({len(pending)} files updated)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="TensorFold package root for a source-only check")
    args = parser.parse_args()
    if args.root is None:
        spec = importlib.util.find_spec("tensorfold")
        if spec is None or spec.origin is None:
            raise RuntimeError("TensorFold is not installed")
        args.root = Path(spec.origin).parent
    apply(args.root)
