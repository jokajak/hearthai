"""HTTP front end for the shared memory store.

Bind to localhost. The token is the only credential, so anything that can reach
this port can read any store whose token it holds.
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .store import InvalidRequest, MemoryStore, StoreNotFound

MAX_BODY = 1 << 20


class Handler(BaseHTTPRequestHandler):
    store: MemoryStore

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ---- plumbing --------------------------------------------------------
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise InvalidRequest("request body too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRequest(f"body is not valid JSON: {exc}") from exc

    def _token(self, parts: list[str], data: dict, query: dict) -> str:
        header = self.headers.get("X-Store-Token")
        token = header or data.get("token") or (query.get("token") or [None])[0]
        if len(parts) >= 2 and parts[0] == "stores" and not token:
            token = parts[1]
        if not token:
            raise InvalidRequest("a store token is required")
        return token

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.strip("/").split("/") if p]
        query = parse_qs(url.query)
        data = self._read_json() if method == "POST" else {}

        if parts == ["health"]:
            return self._send(200, {"ok": True})

        if method == "POST" and parts == ["stores"]:
            result = self.store.create_store(
                data.get("purpose", ""), data.get("author", "unknown")
            )
            return self._send(201, result)

        if parts and parts[0] == "stores":
            token = self._token(parts, data, query)
            tail = parts[2:] if len(parts) >= 2 else []

            if method == "GET" and not tail:
                return self._send(200, self.store.describe(token))

            if method == "GET" and tail == ["entries"]:
                q = (query.get("q") or [""])[0]
                limit = int((query.get("limit") or ["10"])[0])
                return self._send(200, {"entries": self.store.search(token, q, limit)})

            if method == "POST" and tail == ["entries"]:
                result = self.store.add_entry(
                    token,
                    data.get("content", ""),
                    data.get("author", "unknown"),
                    data.get("tags"),
                )
                return self._send(200 if result.get("duplicate") else 201, result)

        self._send(404, {"error": "no such route"})

    def _guard(self, method: str) -> None:
        try:
            self._dispatch(method)
        except StoreNotFound as exc:
            self._send(404, {"error": str(exc)})
        except InvalidRequest as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface as 500, keep serving
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self):  # noqa: N802
        self._guard("GET")

    def do_POST(self):  # noqa: N802
        self._guard("POST")


def build_server(root: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"store": MemoryStore(root)})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="hearthai shared memory service")
    parser.add_argument("--root", default=os.environ.get("HEARTHMEM_ROOT", "./data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = build_server(Path(args.root).expanduser(), args.host, args.port)
    print(f"hearthmem serving {Path(args.root).resolve()} on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
