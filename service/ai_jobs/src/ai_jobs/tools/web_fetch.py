"""Versioned, model-facing webfetch contract and policy.

This is the thin control-plane half of ``web_fetch:v1``. Everything that touches
a socket, a charset, a rule engine or an HTML parser lives in the Rust workers
under ``service/web_fetch/``; what happens here is admission of the request and
validation of the result before either crosses a trust boundary.

The two halves are held together by the shared fixture corpus rather than by a
shared library, so a disagreement about any document fails on one side or the
other instead of becoming a silent difference between the validator that admits
a request and the validator that admits a result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
from ai_jobs.envelope import (
    EnvelopeError,
    EnvelopeErrorCode,
    EnvelopeStatus,
    Inspection,
    InspectionStatus,
    ToolResultEnvelope,
    Trust,
)

MAX_URL_CHARS = 2_048
MAX_CONTENT_BYTES = 1_048_576
ALLOWED_PORTS = (80, 443)
POLICY_ID = "web-content-v1"

OutputFormat = Literal["markdown", "text", "html"]
FORMATS: tuple[OutputFormat, ...] = ("markdown", "text", "html")

#: Server-owned identifiers for the converter that produced the content. The
#: model chooses a format; it never chooses a converter, and no converter
#: returns a URL, a tool call, or an instruction in this field.
CONVERSION_METHODS = ("native", "extracted", "basic", "passthrough", "source")

#: The text media types v1 processes. Anything needing a binary parser or a
#: browser engine is refused before it is stored.
SUPPORTED_MEDIA_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/csv",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
)


def validate_public_url(value: object, path: str) -> str:
    """Syntactic admission for a URL the tool is willing to talk about.

    Whether the destination is *reachable* is a separate question that needs DNS
    answers and the deployment's cluster ranges; the fetch worker's destination
    policy answers that one.
    """
    url = require_string(value, path, MAX_URL_CHARS)
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ContractError(f"{path} must use the http or https scheme")
    if parsed.username or parsed.password:
        raise ContractError(f"{path} must not carry userinfo")
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ContractError(f"{path} must be an absolute URL") from exc
    if not hostname:
        raise ContractError(f"{path} must name a host")
    if (port if port is not None else (443 if parsed.scheme == "https" else 80)) not in ALLOWED_PORTS:
        raise ContractError(f"{path} must use port 80 or 443")
    return url


@dataclass(frozen=True, slots=True)
class WebFetchRequest:
    url: str
    format: OutputFormat = "markdown"

    @classmethod
    def from_dict(cls, value: object) -> "WebFetchRequest":
        data = require_object(value, "request")
        # Anything beyond these two keys - headers, credentials, rules, a
        # skip-scan flag, an image, a command - is a request to reconfigure the
        # worker, and is refused before anything is executed.
        require_exact_fields(data, {"url"}, "request", {"format"})
        url = validate_public_url(data["url"], "request.url")
        output_format = data.get("format", "markdown")
        if output_format not in FORMATS:
            raise ContractError("request.format must be markdown, text, or html")
        return cls(url=url, format=output_format)

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "format": self.format}


@dataclass(frozen=True, slots=True)
class WebFetchData:
    source_id: str
    final_url: str
    http_status: int
    content_type: str
    retrieved_at: str
    format: OutputFormat
    conversion_method: str
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
        if not _is_source_id(source_id):
            raise ContractError("data.source_id is not a run-scoped source identifier")
        # final_url is remote-influenced - redirects choose it - so it gets the
        # same admission as the requested URL.
        final_url = validate_public_url(data["final_url"], "data.final_url")
        http_status = data["http_status"]
        if isinstance(http_status, bool) or not isinstance(http_status, int):
            raise ContractError("data.http_status must be an integer")
        if not 100 <= http_status <= 599:
            raise ContractError("data.http_status is not an HTTP status code")
        content_type = require_string(data["content_type"], "data.content_type", 128)
        if content_type not in SUPPORTED_MEDIA_TYPES:
            raise ContractError("data.content_type is not a supported media type")
        retrieved_at = require_string(data["retrieved_at"], "data.retrieved_at", 40)
        try:
            parsed = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("data.retrieved_at is not an RFC 3339 timestamp") from exc
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ContractError("data.retrieved_at must be UTC")
        output_format = data["format"]
        if output_format not in FORMATS:
            raise ContractError("data.format must be markdown, text, or html")
        conversion_method = data["conversion_method"]
        if conversion_method not in CONVERSION_METHODS:
            raise ContractError("data.conversion_method is not a known conversion method")
        content = data["content"]
        if not isinstance(content, str):
            raise ContractError("data.content must be a string")
        if len(content.encode()) > MAX_CONTENT_BYTES:
            raise ContractError("data.content is longer than the contract allows")
        return cls(
            source_id=source_id,
            final_url=final_url,
            http_status=http_status,
            content_type=content_type,
            retrieved_at=retrieved_at,
            format=output_format,
            conversion_method=conversion_method,
            content=content,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "retrieved_at": self.retrieved_at,
            "format": self.format,
            "conversion_method": self.conversion_method,
            "content": self.content,
        }


def _is_source_id(value: str) -> bool:
    suffix = value.removeprefix("source-")
    return (
        value.startswith("source-")
        and 1 <= len(suffix) <= 32
        and all(character.islower() or character.isdigit() for character in suffix)
        and suffix.isascii()
    )


def validate_envelope(value: object) -> ToolResultEnvelope:
    """Validates a worker result before any of it can reach model context."""
    return ToolResultEnvelope.from_dict(
        value,
        tool=WebFetchDefinition.key,
        tool_version=WebFetchDefinition.version,
        validate_data=WebFetchData.from_dict,
    )


def admitted(data: WebFetchData, policy_id: str = POLICY_ID) -> ToolResultEnvelope:
    """The only way to build a success: a completed no_match against a policy."""
    return ToolResultEnvelope(
        tool=WebFetchDefinition.key,
        tool_version=WebFetchDefinition.version,
        status=EnvelopeStatus.OK,
        trust=Trust.EXTERNAL_UNTRUSTED,
        inspection=Inspection(InspectionStatus.NO_MATCH, policy_id),
        data=data,
        error=None,
    )


def withheld(code: EnvelopeErrorCode, inspection: Inspection) -> ToolResultEnvelope:
    """Every outcome that releases nothing, rejection included."""
    rejected = code is EnvelopeErrorCode.CONTENT_REJECTED
    envelope = ToolResultEnvelope(
        tool=WebFetchDefinition.key,
        tool_version=WebFetchDefinition.version,
        status=EnvelopeStatus.REJECTED if rejected else EnvelopeStatus.ERROR,
        trust=Trust.EXTERNAL_UNTRUSTED,
        inspection=inspection,
        data=None,
        error=EnvelopeError(code),
    )
    envelope._check()
    return envelope


class WebFetchDefinition:
    key = "web_fetch"
    version = 1
    worker_profile = "web-fetch-v1"

    def validate_request(self, value: object) -> WebFetchRequest:
        return WebFetchRequest.from_dict(value)

    def validate_result(self, value: object) -> ToolResultEnvelope:
        return validate_envelope(value)

    def grants_for(self, request: object) -> frozenset[str]:
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        # One grant. No search, no inference, no second destination.
        return frozenset({"fetch"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        # One response, and a whole-run deadline inside the tool integration
        # timeout: 15s of network time plus 5s of inspection, plus pod startup.
        return BrokerBudgets(
            search_calls=0,
            fetch_calls=1,
            fetched_bytes=MAX_CONTENT_BYTES,
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=45,
        )

    def durable_request(self, request: object) -> dict[str, str]:
        """What the run store may keep for this tool: not the URL.

        A run record outlives the fetch, and a list of every URL the model was
        pointed at is exactly the durable content the design says not to
        accumulate. The request digest already gives idempotency and
        correlation; the executor gets the real URL and nothing else does.
        """
        if not isinstance(request, WebFetchRequest):
            raise TypeError("expected WebFetchRequest")
        return {"format": request.format}

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        """Collapses a worker failure onto the public code table.

        A worker's own message never survives this: it would be the one place a
        withheld response could describe itself to the model.
        """
        code = _PUBLIC_FAILURES.get(failure.code, FailureCode.INTERNAL_ERROR)
        return ToolFailure(code=code, message=_PUBLIC_FAILURE_MESSAGES[code])


_PUBLIC_FAILURES = {
    FailureCode.INVALID_REQUEST: FailureCode.INVALID_REQUEST,
    FailureCode.UNSAFE_SOURCE: FailureCode.UNSAFE_SOURCE,
    FailureCode.DEADLINE_EXCEEDED: FailureCode.DEADLINE_EXCEEDED,
    FailureCode.TEMPORARILY_UNAVAILABLE: FailureCode.TEMPORARILY_UNAVAILABLE,
}

_PUBLIC_FAILURE_MESSAGES = {
    FailureCode.INVALID_REQUEST: "The request did not match the web_fetch:v1 contract.",
    FailureCode.UNSAFE_SOURCE: "The destination is not an allowed public web address.",
    FailureCode.DEADLINE_EXCEEDED: "The fetch did not finish within its deadline.",
    FailureCode.TEMPORARILY_UNAVAILABLE: "The fetch capability is not available.",
    FailureCode.INTERNAL_ERROR: "The fetch could not be completed.",
}
