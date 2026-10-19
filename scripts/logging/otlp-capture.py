"""Minimal OTLP/HTTP logs receiver for tests: decodes protobuf requests to JSON lines.

Usage: python otlp-capture.py <output.jsonl>. Listens on :4318 and answers 200.
"""
import http.server
import json
import struct
import sys

OUT = sys.argv[1]


def varint(b, i):
    n = shift = 0
    while True:
        v = b[i]
        i += 1
        n |= (v & 127) << shift
        if not v & 128:
            return n, i
        shift += 7


def fields(b):
    i, out = 0, []
    while i < len(b):
        tag, i = varint(b, i)
        kind, key = tag & 7, tag >> 3
        if kind == 0:
            v, i = varint(b, i)
        elif kind == 1:
            v, i = int.from_bytes(b[i:i + 8], 'little'), i + 8
        elif kind == 2:
            n, i = varint(b, i)
            v, i = b[i:i + n], i + n
        elif kind == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(kind)
        out.append((key, v))
    return out


def any_value(b):
    for key, v in fields(b):
        if key == 1:
            return v.decode()
        if key == 2:
            return bool(v)
        if key == 3:
            return v - (1 << 64) if v >= 1 << 63 else v
        if key == 4:
            return struct.unpack('<d', v.to_bytes(8, 'little'))[0]
        if key == 5:
            return [any_value(x) for k, x in fields(v) if k == 1]
        if key == 6:
            return key_values(v, 1)
        return v.hex()
    return None


def key_values(b, number):
    out = {}
    for key, v in fields(b):
        if key == number:
            kv = dict(fields(v))
            out[kv[1].decode()] = any_value(kv.get(2, b''))
    return out


def decode(b):
    rows = []
    for key, resource_logs in fields(b):
        if key != 1:
            continue
        parts = fields(resource_logs)
        resource = next((key_values(v, 1) for k, v in parts if k == 1), {})
        for k, scope_logs in parts:
            if k != 2:
                continue
            for q, record in fields(scope_logs):
                if q != 2:
                    continue
                row = {'resource': resource, 'attributes': key_values(record, 6)}
                for j, v in fields(record):
                    if j == 1:
                        row['timestamp'] = v
                    elif j == 2:
                        row['severity_number'] = v
                    elif j == 3:
                        row['severity_text'] = v.decode()
                    elif j == 5:
                        row['body'] = any_value(v)
                rows.append(row)
    return rows


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        rows = decode(body)
        header = self.headers.get('VL-Stream-Fields')
        ignore = self.headers.get('VL-Ignore-Fields')
        with open(OUT, 'a') as f:
            for row in rows:
                row['stream_fields'] = header
                row['ignore_fields'] = ignore
                f.write(json.dumps(row) + '\n')
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(('0.0.0.0', 4318), Handler).serve_forever()
