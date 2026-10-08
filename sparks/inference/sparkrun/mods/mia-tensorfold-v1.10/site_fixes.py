#!/usr/bin/env python3
"""Guard exact tool turns and expose failed concurrent engines to SparkRun."""
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
    text = replace_once(text,
        '    return [i for i in ids if isinstance(i, str) and _OUR_ID.match(i)] + [signature(history, calls)]',
        '    return [i for i in ids if isinstance(i, str) and _OUR_ID.match(i)]')
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


def http(text):
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
