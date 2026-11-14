"""``ai-jobs serve``: the HTTP surface of the control plane.

Standard library ``http.server``, like hearthmem next to it. The surface is one POST route, two
probes and a document; a framework would add a dependency to a security boundary for nothing
this file does not already do in a few lines. Threads are cheap here: a request blocks while
its run is waited for (spec E8), and in-flight runs are capped well below any real load.

Routes:

* ``GET /healthz``, ``GET /readyz``: probes, no credentials.
* ``GET /openapi.json``: the tool server document for Open WebUI, no credentials.
* ``POST /v1/tools/memory-recall``: ``Authorization: Bearer <caller key>``, body
  ``{"query": ..., "limit": ...}``. Replies with the validated envelope or a fixed failure.

Nothing a worker, the cluster, hearthmem or a validator said reaches a response body, except
the caller's own request errors from the tool's validator (which describe the caller's input).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from ai_jobs.contracts import ContractError, FailureCode, ToolFailure
from ai_jobs.openapi import openapi_document
from ai_jobs.profiles import memory_recall_profile
from ai_jobs.runs import InvalidIdempotencyKey, RunService, UnknownRun, fixed_failure
from ai_jobs.storage import IdempotencyConflict

MAX_BODY_BYTES = 4_096
MAX_KEY_FILE_BYTES = 65_536
MIN_KEY_CHARS = 32
MAX_ERROR_CHARS = 300
DEFAULT_MAX_CONCURRENT_RUNS = 4

TOOL_ROUTE = "/v1/tools/memory-recall"
TOOL_KEY = "memory_recall"
TOOL_VERSION = 1

_CALLER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_CREDENTIAL_NAME = re.compile(r"[a-z0-9_]{1,64}")

_STATUS_FOR_FAILURE = {
    FailureCode.DEADLINE_EXCEEDED: 504,
    FailureCode.TEMPORARILY_UNAVAILABLE: 503,
}


class ConfigError(ValueError):
    """Startup configuration is unusable. The message never contains a key."""


@dataclass(frozen=True, slots=True)
class CallerKeys:
    """Bearer key -> caller id. Only digests are kept; lookups compare every entry."""

    _digests: tuple[tuple[bytes, str], ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "CallerKeys":
        if not mapping:
            raise ConfigError("no caller keys are configured")
        entries = []
        for key, caller in mapping.items():
            if not isinstance(key, str) or len(key) < MIN_KEY_CHARS or key != key.strip():
                raise ConfigError(f"caller keys must be at least {MIN_KEY_CHARS} characters, without padding")
            if not isinstance(caller, str) or not _CALLER_ID.fullmatch(caller):
                raise ConfigError("caller ids must be lowercase letters, digits and hyphens")
            entries.append((sha256(key.encode("utf-8")).digest(), caller))
        if len({digest for digest, _ in entries}) != len(entries):
            raise ConfigError("duplicate caller key")
        return cls(tuple(entries))

    @classmethod
    def from_file(cls, path: Path) -> "CallerKeys":
        """A JSON object ``{"<key>": "<caller id>"}``, mounted from a Secret."""
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConfigError("the caller key file cannot be read") from exc
        if len(raw) > MAX_KEY_FILE_BYTES:
            raise ConfigError("the caller key file is too large")
        try:
            mapping = json.loads(raw)
        except ValueError as exc:
            raise ConfigError("the caller key file is not valid JSON") from exc
        if not isinstance(mapping, dict):
            raise ConfigError("the caller key file must be a JSON object")
        return cls.from_mapping(mapping)

    def caller_for(self, presented: str) -> str | None:
        digest = sha256(presented.encode("utf-8", "replace")).digest()
        found = None
        for known, caller in self._digests:  # no early exit: timing does not say which entry matched
            if hmac.compare_digest(known, digest):
                found = caller
        return found


def credential_resolver(directory: Path) -> Callable[[str], bytes]:
    """Resolve a profile's ``credential_source`` to a file in the control plane's mounted Secret."""

    def resolve(source: str) -> bytes:
        if not _CREDENTIAL_NAME.fullmatch(source):
            raise ValueError("credential source is not a usable name")
        value = (directory / source).read_bytes().strip()
        if not value:
            raise ValueError("credential is empty")
        return value

    return resolve


class App:
    """What the handler needs: the run service, the caller keys and a cap on in-flight runs."""

    def __init__(
        self,
        runs: RunService,
        callers: CallerKeys,
        *,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
    ):
        self.runs = runs
        self.callers = callers
        self.ready = threading.Event()
        self._slots = threading.BoundedSemaphore(max_concurrent_runs)

    def try_acquire(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release(self) -> None:
        self._slots.release()


class Handler(BaseHTTPRequestHandler):
    app: App
    timeout = 20  # socket reads only; waiting for a run does not touch the socket

    def log_message(self, fmt, *args):  # request lines carry nothing worth keeping; audit has the runs
        pass

    # ---- plumbing ---------------------------------------------------------
    def _send(self, code: int, payload: object, headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _failure(self, code: int, failure_code: FailureCode, message: str, headers=None) -> None:
        self._send(code, ToolFailure(failure_code, message[:MAX_ERROR_CHARS]).to_dict(), headers)

    def _bearer(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        return value.strip()

    # ---- routes -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, {"ok": True})
        if path == "/readyz":
            if self.app.ready.is_set():
                return self._send(200, {"ready": True})
            return self._send(503, {"ready": False})
        if path == "/openapi.json":
            return self._send(200, openapi_document())
        if path == TOOL_ROUTE:
            return self._send(405, {"error": "method not allowed"}, {"Allow": "POST"})
        self._send(404, {"error": "no such route"})

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != TOOL_ROUTE:
            return self._send(404, {"error": "no such route"})
        # Authentication comes before the body is read or anything is validated.
        presented = self._bearer()
        caller = None if presented is None else self.app.callers.caller_for(presented)
        if caller is None:
            return self._failure(
                401, FailureCode.INVALID_REQUEST, "A valid caller key is required.", {"WWW-Authenticate": "Bearer"}
            )
        try:
            self._recall(caller)
        except Exception:  # noqa: BLE001 - never describe an internal error to a caller
            self._failure(500, FailureCode.INTERNAL_ERROR, fixed_failure(FailureCode.INTERNAL_ERROR).message)

    def _recall(self, caller: str) -> None:
        if self.headers.get("Transfer-Encoding"):
            return self._failure(400, FailureCode.INVALID_REQUEST, "A Content-Length body is required.")
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self._failure(400, FailureCode.INVALID_REQUEST, "A Content-Length body is required.")
        if length < 0:
            return self._failure(400, FailureCode.INVALID_REQUEST, "A Content-Length body is required.")
        if length > MAX_BODY_BYTES:
            self.close_connection = True  # the oversized body is not read
            return self._failure(413, FailureCode.INVALID_REQUEST, "The request body is too large.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._failure(400, FailureCode.INVALID_REQUEST, "The request body is not valid JSON.")

        key = self.headers.get("Idempotency-Key")
        key = str(uuid4()) if key is None else key

        if not self.app.try_acquire():
            return self._failure(
                503,
                FailureCode.TEMPORARILY_UNAVAILABLE,
                "The tool is busy. Try again shortly.",
                {"Retry-After": "5"},
            )
        try:
            self._run(caller, key, payload)
        finally:
            self.app.release()

    def _run(self, caller: str, key: str, payload: object) -> None:
        runs = self.app.runs
        try:
            run, _created = runs.admit(caller, TOOL_KEY, TOOL_VERSION, key, payload)
        except InvalidIdempotencyKey as exc:
            return self._failure(400, FailureCode.INVALID_REQUEST, str(exc))
        except ContractError as exc:  # the caller's own request, described by the tool's validator
            return self._failure(400, FailureCode.INVALID_REQUEST, str(exc))
        except IdempotencyConflict:
            return self._failure(
                409, FailureCode.INVALID_REQUEST, "Idempotency-Key was already used for different content."
            )
        except Exception:  # noqa: BLE001 - the executor or cluster failed to start the run
            failure = fixed_failure(FailureCode.TEMPORARILY_UNAVAILABLE)
            return self._failure(503, failure.code, failure.message)

        try:
            outcome = runs.await_result(run.id)
        except UnknownRun:  # a replayed key whose first attempt never started
            failure = fixed_failure(FailureCode.TEMPORARILY_UNAVAILABLE)
            return self._failure(503, failure.code, failure.message)
        if outcome.failure is not None:
            status = _STATUS_FOR_FAILURE.get(outcome.failure.code, 500)
            return self._send(status, outcome.failure.to_dict())
        self._send(200, outcome.result.to_dict())


def build_server(app: App, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


# ---- process wiring ----------------------------------------------------------


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def build_app_from_env() -> tuple[App, Callable[[], None]]:
    """The real wiring: in-cluster client, OTel audit from ``OTEL_*``, keys and token from files."""
    from ai_jobs.audit import Audit
    from ai_jobs.cluster import KubernetesClusterApi
    from ai_jobs.executor import ResolvedWorkerProfile
    from ai_jobs.registry import ToolRegistry
    from ai_jobs.sandbox import NAMESPACE, SandboxExecutor
    from ai_jobs.storage import InMemoryRunStore
    from ai_jobs.tools.memory_recall import MemoryRecallDefinition

    callers = CallerKeys.from_file(Path(_required("AI_JOBS_CALLER_KEYS_FILE")))
    resolver = credential_resolver(Path(_required("AI_JOBS_CREDENTIALS_DIR")))
    profile = memory_recall_profile(_required("AI_JOBS_IMAGE"), _required("AI_JOBS_HEARTHMEM_URL"))
    profiles: dict[str, ResolvedWorkerProfile] = {profile.name: profile}
    resolver(profile.credential_source or "")  # fail at startup, not on the first request

    executor = SandboxExecutor(KubernetesClusterApi(os.environ.get("AI_JOBS_NAMESPACE", NAMESPACE)))
    audit = Audit.from_env()
    runs = RunService(
        ToolRegistry((MemoryRecallDefinition(),)),
        InMemoryRunStore(),
        executor,
        profiles,
        audit=audit,
        credential_resolver=resolver,
    )
    executor.sweep_stale(
        now=datetime.now(UTC),
        longest_deadline_seconds=max(p.active_deadline_seconds for p in profiles.values()),
    )
    return App(runs, callers), audit.shutdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-jobs", description="HearthAI isolated tool control plane")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP surface")
    serve.add_argument("--host", default=os.environ.get("AI_JOBS_HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("AI_JOBS_PORT", "8080")))
    args = parser.parse_args(argv)

    try:
        app, shutdown = build_app_from_env()
    except (ConfigError, ValueError, OSError) as exc:
        print(f"ai-jobs: configuration error: {exc}", file=sys.stderr)
        return 2
    server = build_server(app, args.host, args.port)
    app.ready.set()

    def stop(signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
