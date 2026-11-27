import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

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


def test_cache_salt_is_forwarded_when_all_requests_match(tmp_path, monkeypatch):
    paths = [request(tmp_path, name, cache_salt="tenant-a") for name in ("a", "b")]
    calls = []

    def post(url, route, body):
        calls.append((route, body))
        if route == "/tokenize":
            return {"tokens": [7, 8, 9], "count": 3}
        return {"usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    monkeypatch.setattr(primer, "post", post)
    result = primer.prime("http://spark", paths, 2)
    assert result["primed"] is True
    assert calls[-1] == ("/v1/completions", {"model": "GLM-5.3-Flash", "prompt": [7, 8, 9],
                                              "temperature": 0, "max_tokens": 1, "cache_salt": "tenant-a"})


@pytest.mark.parametrize(("salts", "match"), [
    (({"cache_salt": "tenant-a"}, {"cache_salt": "tenant-b"}), "same cache_salt"),
    (({"cache_salt": "tenant-a"}, {}), "same cache_salt"),
    (({}, {"cache_salt": "tenant-a"}), "same cache_salt"),
    (({"cache_salt": ""}, {"cache_salt": ""}), "non-empty string"),
    (({"cache_salt": 7}, {"cache_salt": 7}), "non-empty string"),
])
def test_cache_salt_mismatches_and_invalid_values_are_rejected(tmp_path, monkeypatch, salts, match):
    paths = [request(tmp_path, f"r{i}", **extra) for i, extra in enumerate(salts)]
    monkeypatch.setattr(primer, "post", lambda url, route, body: {"tokens": [1, 2], "count": 2})
    with pytest.raises(ValueError, match=match):
        primer.prime("http://spark", paths, 1)


@pytest.mark.parametrize(("tokenize_reply", "completion_reply", "match"), [
    ([1, 2], None, "JSON object from /tokenize"),
    ({"tokens": [1], "count": True}, None, "integer token IDs"),
    ({"tokens": [1], "count": 1}, ["not-an-object"], "JSON object from /v1/completions"),
    ({"tokens": [1], "count": 1}, {"usage": None}, "usage object"),
    ({"tokens": [1], "count": 1}, {"usage": {"prompt_tokens": True}}, "integer prompt_tokens"),
])
def test_response_shapes_and_boolean_counts_are_strictly_validated(tmp_path, monkeypatch, tokenize_reply, completion_reply, match):
    paths = [request(tmp_path, name) for name in ("a", "b")]

    def post(url, route, body):
        return tokenize_reply if route == "/tokenize" else completion_reply

    monkeypatch.setattr(primer, "post", post)
    with pytest.raises(ValueError, match=match):
        primer.prime("http://spark", paths, 1)


def test_post_wraps_http_transport_and_json_errors_with_route(monkeypatch):
    def raise_http(req, timeout):
        raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"engine parked"))

    monkeypatch.setattr(primer, "urlopen", raise_http)
    with pytest.raises(RuntimeError, match=r"POST /tokenize failed with HTTP 503: engine parked"):
        primer.post("http://spark", "/tokenize", {"messages": []})

    def raise_url(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(primer, "urlopen", raise_url)
    with pytest.raises(RuntimeError, match=r"POST /v1/completions failed: .*connection refused"):
        primer.post("http://spark", "/v1/completions", {})

    def raise_timeout(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(primer, "urlopen", raise_timeout)
    with pytest.raises(RuntimeError, match=r"POST /v1/completions failed: timed out"):
        primer.post("http://spark", "/v1/completions", {})

    class BadJSONResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(primer, "urlopen", lambda req, timeout: BadJSONResponse(b"not-json"))
    with pytest.raises(ValueError, match=r"POST /tokenize returned invalid JSON"):
        primer.post("http://spark", "/tokenize", {})
