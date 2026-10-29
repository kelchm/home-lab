"""Disposable TLS stand-in, never Hermes or a production credential."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import signal
import ssl
import subprocess
import sys
import threading
import time

mode = os.environ.get("FIXTURE_MODE", "good")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
if mode == "exit":
    sys.exit(1)
started = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bad = mode == "serve-fail" or (mode == "slow" and time.monotonic() - started > 3)
        self.send_response(503 if bad else 200)
        self.end_headers()
        self.wfile.write(b"fixture")

    def log_message(self, *_):
        pass


def leak():
    time.sleep(3)
    for _ in range(40):
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])


if mode == "leak":
    threading.Thread(target=leak, daemon=True).start()
server = HTTPServer(("0.0.0.0", 8443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/tls/cert.pem", "/tls/key.pem")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
