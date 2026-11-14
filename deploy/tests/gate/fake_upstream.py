"""Scripted stand-in for an Ollama host. Standard library only.

Endpoints (all HTTP/1.1, threaded so the probe is answered while a request hangs):
  /api/version          the gate's probe; status from --version-status; counted separately
  /_stats               JSON {"version": n, "proxied": n, "version_times": [wall-clock of each probe]} (never counted itself)
  /hang                 accepts, reads the request, never answers
  /ok                   200 with a small body
  /error                500 with its own JSON body (an upstream's own HTTP error)
  /drop                 chunked headers and three chunks, then a TCP reset
  /stream?silence=S&n=N&interval=I
                        S seconds of silence before any header, then N chunked
                        lines {"i": k, "sent": monotonic} every I seconds
Everything except /api/version and /_stats counts as a proxied request.
"""
import argparse
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATS = {"version": 0, "proxied": 0, "version_times": []}
LOCK = threading.Lock()
ARGS = None


def chunk(handler, payload: bytes):
    handler.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
    handler.wfile.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, status, body: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def start_chunked(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/version":
            with LOCK:
                STATS["version"] += 1
                STATS["version_times"].append(time.time())
            self.send_body(ARGS.version_status, b'{"version":"fake"}')
            return
        if url.path == "/_stats":
            with LOCK:
                self.send_body(200, json.dumps(STATS).encode())
            return
        with LOCK:
            STATS["proxied"] += 1
        if url.path == "/hang":
            time.sleep(600)
        elif url.path == "/ok":
            self.send_body(200, b'{"ok":true}')
        elif url.path == "/error":
            self.send_body(500, b'{"error":"the upstream\'s own error"}')
        elif url.path == "/drop":
            self.start_chunked()
            for i in range(3):
                chunk(self, json.dumps({"i": i}).encode() + b"\n")
                time.sleep(0.2)
            # RST rather than FIN, without the terminating zero-length chunk.
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            self.close_connection = True
        elif url.path == "/stream":
            time.sleep(float(query.get("silence", 0)))
            self.start_chunked()
            interval = float(query.get("interval", 1))
            for i in range(int(query.get("n", 10))):
                chunk(self, json.dumps({"i": i, "sent": time.monotonic()}).encode() + b"\n")
                time.sleep(interval)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        else:
            self.send_body(404, b"{}")

    do_POST = do_GET


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def main():
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--version-status", type=int, default=200)
    parser.add_argument("--no-listen", action="store_true", help="hold the address, open no port")
    ARGS = parser.parse_args()
    if ARGS.no_listen:
        time.sleep(86400)
    Server(("0.0.0.0", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
