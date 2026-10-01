#!/usr/bin/env python3
"""Port LimeChain 9002/9004 behavior onto Jay's W20 without replacing W17 files.

Stop-token behavior derives from LimeChain/glm-5.3-flash-2xdgx-spark@441de756.
Response stats remain available in full mode; normal agent responses stay small.
"""
import ast
import json
import os
from pathlib import Path


def extra_stop(cfg, model_dir):
    import json
    import os
    from pathlib import Path

    root = Path(model_dir)
    ids = list(cfg.eos)
    generation = root / "generation_config.json"
    if generation.is_file():
        eos = json.loads(generation.read_text()).get("eos_token_id")
        ids.extend(eos if isinstance(eos, list) else [] if eos is None else [eos])
    names = [name.strip() for name in os.environ.get("GLM53_TF_EXTRA_STOP", "<|assistant|>").split(",") if name.strip()]
    if names:
        tokens = json.loads((root / "tokenizer.json").read_text())
        added = {token["content"]: int(token["id"]) for token in tokens.get("added_tokens", [])}
        for name in names:
            if name not in added:
                raise ValueError(f"Unknown extra stop token: {name}")
            ids.append(added[name])
    cfg.eos = tuple(dict.fromkeys(int(value) for value in ids))


def response_stats(stats, mode):
    if mode == "off" or stats is None:
        return None
    if mode == "full":
        return stats
    # Keep only bounded diagnostic values. Per-round lists grow with generation.
    return {key: value for key, value in stats.items()
            if key not in {"keeps", "depths", "drafters", "arms", "stats"}}


def stats_mode(body):
    mode = body.get("tensorfold_stats")
    if mode is None:
        mode = (os.environ.get("GLM53_TF_STATS") or "summary").strip().lower()
    return mode if mode in ("summary", "full", "off") else "summary"


def source_of(name):
    source = Path(__file__).read_text()
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.get_source_segment(source, node)


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"TensorFold W20 source drift: {old!r}")
    return text.replace(old, new, 1)


def patch_engine(text):
    text = replace_once(text, "        self.w = w\n",
                        "        self.w = w\n        extra_stop(w.cfg, model_dir)\n")
    return text + "\n\n" + source_of("extra_stop") + "\n"


def patch_server(text):
    text = replace_once(text, "        n = body.get(\"n\")\n",
                        "        mode = body.get(\"tensorfold_stats\")\n"
                        "        if mode is not None and mode not in (\"summary\", \"full\", \"off\"):\n"
                        "            return Problem(\"Invalid tensorfold_stats mode\", param=\"tensorfold_stats\")\n"
                        "        n = body.get(\"n\")\n")
    text = replace_once(text, '                end["tensorfold"] = result["stats"]',
                        '                stats = response_stats(result["stats"], stats_mode(body))\n'
                        '                if stats is not None:\n'
                        '                    end["tensorfold"] = stats')
    if text.count(', "tensorfold": result["stats"]') != 2:
        raise ValueError("TensorFold W20 response anchors drifted")
    text = text.replace(', "tensorfold": result["stats"]', '')
    text = replace_once(text, '            self._json(200, payload)',
                        '            stats = response_stats(result["stats"], stats_mode(body))\n'
                        '            if stats is not None:\n'
                        '                payload["tensorfold"] = stats\n'
                        '            self._json(200, payload)')
    return text + "\n\n" + source_of("response_stats") + "\n\n" + source_of("stats_mode") + "\n"


def main():
    import tensorfold
    root = Path(tensorfold.__file__).parent
    changes = {
        root / "families/glm5_next/cuda/engine.py": patch_engine,
        root / "cuda/server.py": patch_server,
    }
    prepared = {path: transform(path.read_text()) for path, transform in changes.items()}
    for path, text in prepared.items():
        compile(text, str(path), "exec")
    for path, text in prepared.items():
        path.write_text(text)
        for pyc in (path.parent / "__pycache__").glob(path.stem + ".*.pyc"):
            pyc.unlink()
    print("TensorFold W20 stop tokens and bounded response stats verified")


if __name__ == "__main__":
    main()
