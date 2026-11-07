"""Bounded SSE transport with a total deadline, including headers and trickle reads."""
import base64
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from .fixtures import canonical, sha256


MAX_BYTES = 4 * 1024 * 1024


def strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)


class Stream:
    def __init__(self):
        self.buffer = b""
        self.data = []
        self.done = False
        self.finish = None
        self.usage = None
        self.message = {"role": "assistant", "content": "", "reasoning_content": ""}
        self.calls = {}

    def feed(self, chunk):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if line == b"":
                if self.data:
                    self.event(b"\n".join(self.data))
                    self.data = []
            elif line.startswith(b"data:"):
                self.data.append(line[5:].removeprefix(b" "))
            elif line.startswith(b":") or line.startswith((b"event:", b"id:", b"retry:")):
                continue
            else:
                raise ValueError("Malformed SSE field")

    def event(self, payload):
        if self.done:
            raise ValueError("Event after DONE")
        if payload == b"[DONE]":
            self.done = True
            return
        data = strict_json(payload)
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError(f"API error or malformed event: {data!r}")
        if data.get("usage") is not None:
            if self.usage is not None and self.usage != data["usage"]:
                raise ValueError("Conflicting usage receipts")
            self.usage = data["usage"]
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("Expected choices array with at most one choice")
        for choice in choices:
            if type(choice.get("index")) is not int or choice["index"] != 0:
                raise ValueError("Unexpected choice index")
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ValueError("Invalid delta")
            if delta.get("role", "assistant") != "assistant":
                raise ValueError("Unexpected streamed role")
            if delta.get("reasoning_content") is not None and delta.get("reasoning") is not None:
                if delta["reasoning_content"] != delta["reasoning"]:
                    raise ValueError("Conflicting reasoning aliases")
            if self.finish is not None and (any(delta.values()) or choice.get("finish_reason")):
                raise ValueError("Content or repeated finish after finish")
            for name in ("content", "reasoning_content"):
                value = delta.get(name)
                if name == "reasoning_content":
                    value = value if value is not None else delta.get("reasoning")
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("Non-text delta")
                    self.message[name] += value
            for part in delta.get("tool_calls") or []:
                index = part.get("index")
                if type(index) is not int or index < 0 or index > 8:
                    raise ValueError("Invalid tool index")
                call = self.calls.setdefault(index, {"id": "", "type": "function",
                                                     "function": {"name": "", "arguments": ""}})
                if part.get("type", "function") != "function":
                    raise ValueError("Invalid tool type")
                call["id"] += part.get("id") or ""
                function = part.get("function") or {}
                for name in ("name", "arguments"):
                    call["function"][name] += function.get(name) or ""
            if choice.get("finish_reason") is not None:
                self.finish = choice["finish_reason"]

    def receipt(self):
        if not self.done or self.buffer or self.data or self.finish not in ("stop", "tool_calls"):
            raise ValueError("Incomplete stream: affirmative DONE and stop/tool_calls finish required")
        usage = self.usage
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] <= 0
                                              for k in ("prompt_tokens", "completion_tokens")):
            raise ValueError("Affirmative server prompt/completion usage required")
        if "total_tokens" in usage and (type(usage["total_tokens"]) is not int or
                                        usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]):
            raise ValueError("Inconsistent total usage")
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if cached is not None and (type(cached) is not int or not 0 <= cached <= usage["prompt_tokens"]):
            raise ValueError("Invalid cached token count")
        if self.calls:
            if sorted(self.calls) != list(range(len(self.calls))):
                raise ValueError("Sparse tool indices")
            self.message["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        return {"message": self.message, "finish_reason": self.finish, "done": True,
                "usage": usage, "cached_tokens": cached}


def parse(raw):
    stream = Stream()
    stream.feed(raw)
    return stream.receipt()


def endpoint(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Endpoint must be an explicit HTTP(S) base URL without credentials/query/fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1/chat/completions"):
        pass
    elif path.endswith("/v1"):
        path += "/chat/completions"
    else:
        path += "/v1/chat/completions"
    return parsed, path


def request(url, body, deadline_s, api_key=None):
    """No retry. A daemon bounds DNS/connect too; it never writes evidence or sends after cancellation.

    Socket shutdown interrupts header, partial-line and incomplete HTTP chunk reads.
    The caller returns at its total deadline even if OS DNS resolution is stuck.
    """
    parsed, path = endpoint(url)
    started = time.monotonic()
    state = {"raw": bytearray(), "error": None, "status": None, "content_type": None, "socket": None}
    lock = threading.Lock()
    cancelled = threading.Event()

    def worker():
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=deadline_s)
        try:
            connection.connect()
            with lock:
                state["socket"] = connection.sock
            if cancelled.is_set():
                return
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if api_key:
                headers["Authorization"] = "Bearer " + api_key
            connection.request("POST", path, body=canonical(body), headers=headers)
            response = connection.getresponse()
            with lock:
                state["status"] = response.status
                state["content_type"] = response.getheader("Content-Type", "")
            if response.status != 200 or response.getheader("Content-Type", "").split(";")[0].strip() != "text/event-stream":
                raise ValueError("Expected HTTP 200 text/event-stream")
            stream = Stream()
            while not cancelled.is_set():
                chunk = response.read1(8192)
                if not chunk:
                    break
                with lock:
                    if len(state["raw"]) + len(chunk) > MAX_BYTES:
                        raise ValueError("Response exceeds bounded receipt size")
                    state["raw"].extend(chunk)
                stream.feed(chunk)
                if stream.done:
                    break
            stream.receipt()
        except Exception as error:
            with lock:
                state["error"] = f"{type(error).__name__}: {error}"
        finally:
            connection.close()

    thread = threading.Thread(target=worker, name="glm-qualification-http", daemon=True)
    thread.start()
    thread.join(max(0, deadline_s - (time.monotonic() - started)))
    timed_out = thread.is_alive() or time.monotonic() - started >= deadline_s
    if timed_out:
        cancelled.set()
        with lock:
            sock = state["socket"]
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        thread.join(0.1)
    with lock:
        raw = bytes(state["raw"])
        return {"elapsed_s": time.monotonic() - started, "deadline_s": deadline_s,
                "deadline_exceeded": timed_out, "http_status": state["status"],
                "content_type": state["content_type"], "response_b64": base64.b64encode(raw).decode(),
                "response_sha256": sha256(raw),
                "error": "DeadlineExceeded: total request deadline" if timed_out else state["error"]}
