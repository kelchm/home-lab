#!/usr/bin/env python3
"""Serve a fixture event list and watch stream the way the Kubernetes API does.

Usage: events-api.py PLAN REQUEST_LOG. PLAN holds `list` (an EventList) and
`watch` (watch events). The first watch streams them as chunked JSON lines and
ends, so the client lists again and watches anew; later watches stay open.
Each request's path and Authorization header are appended to REQUEST_LOG.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

plan = json.load(open(sys.argv[1]))
log_path = sys.argv[2]
lock = threading.Lock()
watches = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        global watches
        with lock, open(log_path, 'a') as f:
            f.write(json.dumps({'path': self.path, 'authorization': self.headers.get('Authorization')}) + '\n')
        if not self.path.startswith('/api/v1/events'):
            self.send_error(404)
            return
        if 'watch=1' not in self.path:
            body = json.dumps(plan['list']).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        with lock:
            watches += 1
            first = watches == 1
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        for event in plan['watch'] if first else []:
            line = json.dumps(event).encode() + b'\n'
            self.wfile.write(b'%x\r\n%s\r\n' % (len(line), line))
            self.wfile.flush()
            time.sleep(0.05)
        if not first:
            time.sleep(300)
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()

    def log_message(self, *args):
        pass


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
