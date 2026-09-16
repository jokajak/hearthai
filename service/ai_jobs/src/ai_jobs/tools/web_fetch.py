"""Versioned, model-facing webfetch contract and policy.

The worker logic is Rust; this is the thin definition the control plane needs to
admit a request, select the fixed profile, and validate what comes back. The
rules here are the same ones ``service/web_fetch/contracts`` enforces, and the
shared fixtures prove it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    FailureCode,
    ToolFailure,
    require_exact_fields,
    require_object,
    require_string,
)
from ai_jobs.envelope import Envelope, ErrorCode, Inspection

TOOL_KEY = "web_fetch"
TOOL_VERSION = 1
WORKER_PROFILE = "web-fetch-v1"

MAX_URL_CHARS = 2_048
MAX_CONTENT_CHARS = 1_048_576
MAX_CONTENT_TYPE_CHARS = 200
#: Whole-run ceiling for both stages, inside the tool integration timeout.
RUN_DEADLINE_SECONDS = 25

Format = Literal["markdown", "text", "html"]
FORMATS: frozenset[str] = frozenset({"markdown", "text", "html"})


class ConversionMethod(StrEnum):
    NATIVE = "native"
    EXTRACTED = "extracted"
    BASIC = "basic"
    PASSTHROUGH = "passthrough"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class WebFetchRequest:
    """A URL, and optionally an output format. Nothing else is accepted.

    There is deliberately no field for headers, cookies, credentials, rules, a
    timeout, a proxy, or a skip-scan flag; unknown fields are refused rather
    than ignored, so a caller cannot smuggle runtime settings past admission.
    """

    url: str
    format: Format = "markdown"

    @classmethod
    def from_dict(cls, value: object) -> "WebFetchRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"url"}, "request", {"format"})
        url = require_string(data["url"], "request.url", MAX_URL_CHARS)
        _check_fetchable(url)
        output = data.get("format", "markdown")
        if output not in FORMATS:
            raise ContractError("request.format must be markdown, text, or html")
        return cls(url=url, format=output)

    def to_dict(self) -> dict[str, object]:
        return {"url": self.url, "format": self.format}

    def storage_view(self) -> dict[str, object]:
        """What durable records may keep: a digest, never the URL itself."""
        return {"url_sha256": hashlib.sha256(self.url.encode()).hexdigest(), "format": self.format}


def _check_fetchable(url: str) -> None:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ContractError("request.url must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ContractError("request.url must not contain userinfo")
    if not parsed.hostname:
        raise ContractError("request.url must have a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ContractError("request.url has an invalid port") from exc
    if port is not None and port not in (80, 443):
        raise ContractError("request.url must use the default web ports")


@dataclass(frozen=True, slots=True)
class WebFetchData:
    """The successful payload.

    Every field except ``source_id`` and the two enums came from the response,
    and the inspection stage scanned all of them - including the final URL and
    the media type - before this could be built.
    """

    source_id: str
    final_url: str
    http_status: int
    content_type: str
    retrieved_at: str
    format: Format
    conversion_method: ConversionMethod
    content: str

    @classmethod
    def from_dict(cls, value: object) -> "WebFetchData":
        data = require_object(value, "data")
        require_exact_fields(
            data,
            {
                "source_id",
                "final_url",
                "http_status",
                "content_type",
                "retrieved_at",
                "format",
                "conversion_method",
                "content",
            },
            "data",
        )
        source_id = require_string(data["source_id"], "data.source_id", 64)
        suffix = source_id.removeprefix("source-")
        if suffix == source_id or not suffix.isdigit():
            raise ContractError("data.source_id must be a run-scoped source identifier")

        final_url = require_string(data["final_url"], "data.final_url", MAX_URL_CHARS)
        parsed = urlsplit(final_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ContractError("data.final_url must be an HTTP(S) URL without userinfo")

        http_status = data["http_status"]
        if isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599:
            raise ContractError("data.http_status is not an HTTP status code")

        content_type = require_string(data["content_type"], "data.content_type", MAX_CONTENT_TYPE_CHARS)
        if not content_type.isascii() or any(ord(character) < 0x20 or ord(character) == 0x7F for character in content_type):
            raise ContractError("data.content_type must be printable ASCII")

        retrieved_at = require_string(data["retrieved_at"], "data.retrieved_at", 40)
        try:
            datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("data.retrieved_at must be an RFC 3339 timestamp") from exc

        output = data["format"]
        if output not in FORMATS:
            raise ContractError("data.format must be markdown, text, or html")
        try:
            method = ConversionMethod(require_string(data["conversion_method"], "data.conversion_method", 32))
        except ValueError as exc:
            raise ContractError("data.conversion_method is not a known conversion method") from exc

        content = require_string(data["content"], "data.content", MAX_CONTENT_CHARS, allow_empty=True)
        if output == "html" and method is not ConversionMethod.SOURCE:
            raise ContractError("html output is only ever returned as inert source")
        return cls(source_id, final_url, http_status, content_type, retrieved_at, output, method, content)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "retrieved_at": self.retrieved_at,
            "format": self.format,
            "conversion_method": self.conversion_method.value,
            "content": self.content,
        }


def validate_envelope(value: object) -> Envelope:
    """Validate a `web_fetch:v1` envelope, payload included."""
    return Envelope.from_dict(
        value,
        tool=TOOL_KEY,
        tool_version=TOOL_VERSION,
        payload=WebFetchData.from_dict,
    )


def failure_envelope(code: ErrorCode, inspection: Inspection) -> Envelope:
    return Envelope.failure(TOOL_KEY, TOOL_VERSION, code, inspection)


class WebFetchDefinition:
    key = TOOL_KEY
    version = TOOL_VERSION
    worker_profile = WORKER_PROFILE

    def validate_request(self, value: object) -> WebFetchRequest:
        return WebFetchRequest.from_dict(value)

    def validate_result(self, value: object) -> Envelope:
        return validate_envelope(value)

    def grants_for(self, request: object) -> frozenset[str]:
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        # One bounded GET. No search, no inference, no second fetch.
        return frozenset({"fetch"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        return BrokerBudgets(
            search_calls=0,
            fetch_calls=1,
            fetched_bytes=1_048_576,
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=RUN_DEADLINE_SECONDS,
        )

    def storage_view(self, request: object) -> dict[str, object]:
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        return request.storage_view()

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        """Clamp a control-plane failure to a fixed public one.

        A worker's own text never reaches a caller: anything that is not one of
        the codes this tool can produce becomes the generic internal failure.
        """
        allowed = {
            FailureCode.INVALID_REQUEST,
            FailureCode.TEMPORARILY_UNAVAILABLE,
            FailureCode.DEADLINE_EXCEEDED,
            FailureCode.UNSAFE_SOURCE,
        }
        if failure.code in allowed:
            return ToolFailure(code=failure.code, message=_PUBLIC_MESSAGES[failure.code])
        return ToolFailure(code=FailureCode.INTERNAL_ERROR, message=_PUBLIC_MESSAGES[FailureCode.INTERNAL_ERROR])


_PUBLIC_MESSAGES = {
    FailureCode.INVALID_REQUEST: "The request is not a valid web_fetch:v1 request.",
    FailureCode.TEMPORARILY_UNAVAILABLE: "The fetch capability is unavailable.",
    FailureCode.DEADLINE_EXCEEDED: "The request did not complete within its deadline.",
    FailureCode.UNSAFE_SOURCE: "The requested destination is not allowed.",
    FailureCode.INTERNAL_ERROR: "The tool could not complete this request.",
}
