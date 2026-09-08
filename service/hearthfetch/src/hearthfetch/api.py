"""The two tools, and the HTTP transport in front of them.

Split deliberately: `ToolService` is the behaviour and holds no HTTP, while the
handler does auth, limits, and dispatch. The security-relevant logic is
therefore testable without a socket, and the socket-level concerns (bearer
comparison, body caps, content types) get their own tests against a real
server.

Stdlib http.server, matching hearthmem: no runtime dependency beyond the
handle sealing, and nothing to keep patched.
"""

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from hearthfetch.config import Config
from hearthfetch.contracts import (
    ContractError,
    FailureCode,
    FetchResultRequest,
    SearchRequest,
    ToolFailure,
)
from hearthfetch.fetch import Connector, FetchError, Resolver, fetch
from hearthfetch.handles import HandleError, open_handle
from hearthfetch.observability import Metrics, audit
from hearthfetch.pipeline import DocumentRejected, Pipeline
from hearthfetch.scrub_out import DistillationDropped
from hearthfetch.scrub_out import scrub as scrub_out
from hearthfetch.search import SearchProvider, search_web

MAX_BODY = 1 << 16
MAX_TITLE_CHARS = 300


class _TitleReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def page_title(html: str) -> str:
    """A page's own title is attacker-authored, so it is scrubbed like any other."""
    reader = _TitleReader()
    try:
        reader.feed(html)
        reader.close()
    except Exception:
        return ""
    raw = reader.title.strip()
    if not raw:
        return ""
    try:
        return scrub_out(raw)[:MAX_TITLE_CHARS]
    except DistillationDropped:
        # A title that cannot be made safe is simply absent. Losing it costs a
        # label; keeping it could cost a destination.
        return ""


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class ToolService:
    config: Config
    pipeline: Pipeline
    search_provider: SearchProvider
    handle_key: bytes
    resolver: Resolver
    connector: Connector
    metrics: Metrics = field(default_factory=Metrics)
    clock: Callable[[], float] = time.time

    # ---------------------------------------------------------------- search
    def search_web(self, body: object) -> tuple[int, dict]:
        try:
            request = SearchRequest.from_dict(body)
        except ContractError as exc:
            self.metrics.increment("hearthfetch_tool_calls", tool="search_web", outcome="invalid")
            return 400, ToolFailure(FailureCode.INVALID_REQUEST, str(exc)).to_dict()

        started = self.clock()
        results = search_web(
            request.query,
            request.count,
            provider=self.search_provider,
            handle_key=self.handle_key,
            now=self.clock(),
        )
        self.metrics.observe("hearthfetch_search_seconds", self.clock() - started)
        self.metrics.increment(
            "hearthfetch_tool_calls",
            tool="search_web",
            outcome="ok" if results else "empty",
        )
        return 200, {"results": [r.to_dict() for r in results]}

    # ---------------------------------------------------------------- fetch
    def fetch_result(self, body: object) -> tuple[int, dict]:
        try:
            request = FetchResultRequest.from_dict(body)
        except ContractError as exc:
            self.metrics.increment("hearthfetch_tool_calls", tool="fetch_result", outcome="invalid")
            return 400, ToolFailure(FailureCode.INVALID_REQUEST, str(exc)).to_dict()

        try:
            handle = open_handle(request.handle, key=self.handle_key, now=self.clock())
        except HandleError:
            # Deliberately indistinguishable from a bad request: forged,
            # expired, and malformed handles must not be separable by a caller
            # probing which one it hit.
            self.metrics.increment("hearthfetch_tool_calls", tool="fetch_result", outcome="invalid")
            return 400, ToolFailure(FailureCode.INVALID_REQUEST, "invalid handle").to_dict()

        started = self.clock()
        try:
            document = fetch(
                handle.url,
                policy=self.config.fetch_policy,
                resolver=self.resolver,
                connector=self.connector,
            )
        except FetchError as exc:
            self.metrics.increment("hearthfetch_fetch_refusals", reason=str(exc.reason))
            self.metrics.increment("hearthfetch_tool_calls", tool="fetch_result", outcome="rejected")
            return 200, ToolFailure(FailureCode.SOURCE_REJECTED, "source rejected").to_dict()
        finally:
            self.metrics.observe("hearthfetch_fetch_seconds", self.clock() - started)

        html = _decode(document.body, document.content_type)
        try:
            distillation = self.pipeline.process(html, request.question, page_title(html))
        except DocumentRejected as exc:
            self.metrics.increment("hearthfetch_source_rejections", stage=str(exc.stage))
            self.metrics.increment("hearthfetch_tool_calls", tool="fetch_result", outcome="rejected")
            # The caller learns the source was rejected, never which stage
            # rejected it: a reason is a bypass oracle.
            return 200, ToolFailure(FailureCode.SOURCE_REJECTED, "source rejected").to_dict()

        self.metrics.increment("hearthfetch_tool_calls", tool="fetch_result", outcome="ok")
        return 200, distillation.to_dict()

    def audit_for(self, **fields: str) -> dict[str, str]:
        return audit(**fields)


def build_handler(service: ToolService, *, token: str, openapi: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hearthfetch"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            header = self.headers.get("Authorization") or ""
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            # Constant-time: a timing-distinguishable comparison leaks the token
            # one byte at a time to anything that can reach the port.
            return hmac.compare_digest(header[len(prefix):], token)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if self.path == "/readyz":
                # Readiness is not liveness: the process can answer long before
                # it has somewhere to send a search.
                ready = bool(service.config.litellm_base_url)
                return self._send(200 if ready else 503, {"status": "ok" if ready else "not_ready"})
            if self.path == "/openapi.json":
                return self._send(200, openapi)
            if self.path == "/metrics":
                body = service.metrics.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._send(404, ToolFailure(FailureCode.INVALID_REQUEST, "not found").to_dict())

        def do_POST(self):  # noqa: N802
            routes = {
                "/v1/tools/search_web": service.search_web,
                "/v1/tools/fetch_result": service.fetch_result,
            }
            handler = routes.get(self.path)
            if handler is None:
                return self._send(
                    404, ToolFailure(FailureCode.INVALID_REQUEST, "not found").to_dict()
                )
            if not self._authorised():
                return self._send(
                    401, ToolFailure(FailureCode.INVALID_REQUEST, "unauthorised").to_dict()
                )
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if content_type != "application/json":
                return self._send(
                    415,
                    ToolFailure(FailureCode.INVALID_REQUEST, "expected application/json").to_dict(),
                )
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(
                    400, ToolFailure(FailureCode.INVALID_REQUEST, "bad length").to_dict()
                )
            if length <= 0 or length > MAX_BODY:
                return self._send(
                    413 if length > MAX_BODY else 400,
                    ToolFailure(FailureCode.INVALID_REQUEST, "bad body size").to_dict(),
                )
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send(
                    400, ToolFailure(FailureCode.INVALID_REQUEST, "body is not valid JSON").to_dict()
                )
            try:
                code, payload = handler(body)
            except Exception:
                # Never let a traceback or an upstream message reach a caller.
                return self._send(
                    500, ToolFailure(FailureCode.INTERNAL_ERROR, "internal error").to_dict()
                )
            return self._send(code, payload)

    return Handler


def build_server(
    service: ToolService,
    *,
    token: str,
    openapi: dict,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), build_handler(service, token=token, openapi=openapi))
