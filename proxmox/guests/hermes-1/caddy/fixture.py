#!/usr/bin/env python3
"""Disposable loopback HTTP/WebSocket upstream and TLS client; stdlib only."""

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PATHS = ("/api/ws", "/api/pty")


def accept(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise ValueError("truncated WebSocket frame")
        data.extend(chunk)
    return bytes(data)


def read_frame(stream, masked):
    first, second = read_exact(stream, 2)
    if not first & 0x80 or first & 0x70 or bool(second & 0x80) != masked:
        raise ValueError("invalid WebSocket flags")
    size = second & 0x7f
    if size == 126:
        size = struct.unpack("!H", read_exact(stream, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", read_exact(stream, 8))[0]
    if size > 4096:
        raise ValueError("oversized fixture frame")
    mask = read_exact(stream, 4) if masked else b""
    payload = read_exact(stream, size)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return first & 0xf, payload


def frame(opcode, payload, masked=False):
    if len(payload) > 125:
        raise ValueError("fixture sends only small frames")
    mask = os.urandom(4) if masked else b""
    body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)) if masked else payload
    return bytes((0x80 | opcode, len(payload) | (0x80 if masked else 0))) + mask + body


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/api/status":
            body = json.dumps({"fixture": "hermes-caddy", "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if (self.path not in PATHS or self.headers.get("Upgrade", "").lower() != "websocket"
                or "upgrade" not in self.headers.get("Connection", "").lower()
                or self.headers.get("Sec-WebSocket-Version") != "13"):
            self.send_error(400)
            return
        if len(base64.b64decode(key, validate=True)) != 16:
            self.send_error(400)
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept(key))
        self.end_headers()
        self.connection.settimeout(5)
        opcode, payload = read_frame(self.rfile, masked=True)
        if opcode not in (1, 2):
            raise ValueError("fixture expects text or binary input")
        self.wfile.write(frame(opcode, self.path.encode() + b":" + payload))
        self.wfile.flush()
        self.close_connection = True


def exchange(connection, path, opcode):
    key = base64.b64encode(os.urandom(16)).decode()
    request = (f"GET {path} HTTP/1.1\r\nHost: caddy.test\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    connection.sendall(request.encode())
    stream = connection.makefile("rb")
    with stream:
        if stream.readline(4096).split()[:2] != [b"HTTP/1.1", b"101"]:
            raise ValueError("WebSocket upgrade failed")
        headers = {}
        for _ in range(64):
            line = stream.readline(4096)
            if line == b"\r\n":
                break
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        else:
            raise ValueError("unbounded response headers")
        if (headers.get("sec-websocket-accept") != accept(key)
                or headers.get("upgrade", "").lower() != "websocket"
                or "upgrade" not in headers.get("connection", "").lower()):
            raise ValueError("invalid WebSocket acceptance")
        payload = os.urandom(16).hex().encode()
        connection.sendall(frame(opcode, payload, masked=True))
        if read_frame(stream, masked=False) != (opcode, path.encode() + b":" + payload):
            raise ValueError("WebSocket path/echo mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("serve", "check"))
    parser.add_argument("--ca")
    args = parser.parse_args()
    if args.mode == "serve":
        ThreadingHTTPServer(("127.0.0.1", 9119), Upstream).serve_forever()
    else:
        context = ssl.create_default_context(cafile=args.ca)
        for path, opcode in zip(PATHS, (1, 2)):
            with socket.create_connection(("127.0.0.1", 443), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname="caddy.test") as connection:
                    exchange(connection, path, opcode)
            print(f"Trusted TLS, valid upgrade and forwarded echo passed: {path}")


if __name__ == "__main__":
    main()
