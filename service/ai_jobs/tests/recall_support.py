"""Shared pieces for the recall tests: a real hearthmem, stub servers, and a cluster that runs the worker."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # service/, for hearthmem

from hearthmem.server import build_server as build_hearthmem  # noqa: E402

from ai_jobs.cluster import FakeClusterApi  # noqa: E402
from ai_jobs.worker import main as worker_main  # noqa: E402


def serve_in_thread(server: ThreadingHTTPServer) -> str:
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def hearthmem_call(base, method, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Store-Token"] = token
    request = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


class Hearthmem:
    """A real hearthmem with one seeded store, a write token and a read token."""

    def __init__(self, root: Path, entries=()):
        self.server = build_hearthmem(root, port=0)
        self.url = serve_in_thread(self.server)
        store = hearthmem_call(self.url, "POST", "/stores", {"purpose": "household notes", "author": "test"})
        self.write_token = store["token"]
        for entry in entries:
            hearthmem_call(self.url, "POST", "/stores/-/entries", entry, token=self.write_token)
        self.read_token = hearthmem_call(self.url, "POST", "/stores/-/read-token", {}, token=self.write_token)[
            "read_token"
        ]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class StubHearthmem:
    """Answers every request with a canned status and body, and records what it was sent."""

    def __init__(self, status=200, body=b'{"entries": []}', delay=0.0):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                outer.requests.append((self.path, dict(self.headers)))
                if delay:
                    threading.Event().wait(delay)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = serve_in_thread(self.server)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class WorkerRunningCluster(FakeClusterApi):
    """The fake cluster, except the pod really runs: the worker executes in-process.

    When the per-run Secret is created, its contents are written to files and the worker
    is called with the container command the Sandbox manifest carries, then the Sandbox
    finishes with whatever the worker printed. Nothing but the Secret and the command reaches it.
    """

    def __init__(self, tmp_path: Path, clock=None):
        super().__init__(clock)
        self.tmp_path = tmp_path
        self.auto_run = True

    def create_secret(self, manifest):
        super().create_secret(manifest)
        if not self.auto_run:
            return
        name = manifest["metadata"]["name"]
        mount = self.tmp_path / name
        mount.mkdir()
        for key, value in manifest["data"].items():
            (mount / key).write_bytes(base64.b64decode(value))
        command = self.sandboxes[name]["spec"]["podTemplate"]["spec"]["containers"][0]["command"]
        assert command[0] == "ai-jobs-worker"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            worker_main(
                command[1:], request_path=mount / "request.json", credential_path=mount / "credential"
            )
        self.finish_succeeded(name, out.getvalue().encode())
