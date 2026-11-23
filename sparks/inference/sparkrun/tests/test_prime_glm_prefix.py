import importlib.util
import json
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / "prime_glm_prefix.py"
spec = importlib.util.spec_from_file_location("prime_glm_prefix", path)
primer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(primer)


def request(tmp_path, name, **changes):
    path = tmp_path / f"{name}.json"
    body = {"model": "GLM-5.3-Flash", "messages": [{"role": "user", "content": name}], **changes}
    path.write_text(json.dumps(body))
    return path


@pytest.mark.parametrize(("minimum", "expected"), [(2, True), (3, False)])
def test_prefix_uses_server_rendering_and_stops_at_first_difference(tmp_path, monkeypatch, minimum, expected):
    paths = [request(tmp_path, name, reasoning_effort="high") for name in ("a", "b", "c")]
    calls = []
    ids = iter(([2, 3, 4, 5], [2, 3, 4], [2, 3, 9, 5]))

    def post(url, route, body):
        calls.append((route, body))
        if route == "/tokenize":
            tokens = next(ids)
            return {"tokens": tokens, "count": len(tokens)}
        return {"usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    monkeypatch.setattr(primer, "post", post)
    result = primer.prime("http://spark", paths, minimum)
    assert result["shared_tokens"] == 2 and result["primed"] is expected
    assert calls[:3] == [("/tokenize", json.loads(path.read_bytes())) for path in paths]
    assert len(calls) == 3 + int(expected)
    if expected:
        assert calls[-1] == ("/v1/completions", {"model": "GLM-5.3-Flash", "prompt": [2, 3],
                                                  "temperature": 0, "max_tokens": 1})


def test_media_is_excluded_from_token_only_priming(tmp_path):
    path = request(tmp_path, "image", messages=[{"role": "user", "content": [{"type": "image_url"}]}])
    with pytest.raises(ValueError, match="text-only"):
        primer.chat_body(path)
