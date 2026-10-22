#!/usr/bin/env python3
"""Serve fixture event lists and a watch stream the way the Kubernetes API does.

Usage: events-api.py PLAN REQUEST_LOG. PLAN holds `lists` (EventLists, served
in order, the last repeated) and `watch` (watch events). The first watch streams
them as chunked JSON lines and ends, as the API server does at timeoutSeconds,
so the client lists again and watches anew; later watches stay open. Each
request's path and Authorization header are appended to REQUEST_LOG.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

plan = json.load(open(sys.argv[1]))
log_path = sys.argv[2]
lock = threading.Lock()
calls = {'list': 0, 'watch': 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        with lock, open(log_path, 'a') as f:
            f.write(json.dumps({'path': self.path, 'authorization': self.headers.get('Authorization')}) + '\n')
        if not self.path.startswith('/api/v1/events'):
            self.send_error(404)
            return
        kind = 'watch' if 'watch=1' in self.path else 'list'
        with lock:
            n = calls[kind]
            calls[kind] += 1
        if kind == 'list':
            body = json.dumps(plan['lists'][min(n, len(plan['lists']) - 1)]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        for event in plan['watch'] if n == 0 else []:
            line = json.dumps(event).encode() + b'\n'
            self.wfile.write(b'%x\r\n%s\r\n' % (len(line), line))
            self.wfile.flush()
            time.sleep(0.05)
        if n > 0:
            time.sleep(300)
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()

    def log_message(self, *args):
        pass


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
