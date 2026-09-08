"""Wire contracts for the model-facing tools.

Deliberately absent from every request type: image, command, namespace, pod,
credential, mount, timeout, model. The privileged model chooses what to ask
about; it never chooses how the work runs.

The validation helpers are lifted from the withdrawn ai_jobs package
(383e94c:service/ai_jobs/src/ai_jobs/contracts.py) rather than rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

MAX_QUERY_CHARS = 512
MAX_QUESTION_CHARS = 1_024
MAX_HANDLE_CHARS = 2_048
MAX_URL_CHARS = 2_048
MAX_RESULTS = 10


class ContractError(ValueError):
    """A value does not conform to a public wire contract."""


class FailureCode(StrEnum):
    """Stable, caller-visible failure categories.

    Deliberately coarse. A precise reason is a bypass oracle: it tells an
    attacker which rule caught them, which is exactly the feedback the design
    denies them by giving no other signal.
    """

    INVALID_REQUEST = "invalid_request"
    SOURCE_REJECTED = "source_rejected"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolFailure:
    code: FailureCode
    message: str

    def to_dict(self) -> dict[str, object]:
        return {"error": {"code": str(self.code), "message": self.message}}


def require_object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def require_exact_fields(
    data: Mapping[str, Any],
    required: set[str],
    field: str,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = required - data.keys()
    if missing:
        raise ContractError(f"{field} is missing {sorted(missing)}")
    unknown = data.keys() - allowed
    if unknown:
        # Unknown fields are rejected rather than ignored: an accepted-and-ignored
        # field is how an infrastructure-shaped parameter creeps into a contract.
        raise ContractError(f"{field} has unknown fields {sorted(unknown)}")


def require_string(value: object, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise ContractError(f"{field} must not be empty")
    if len(trimmed) > max_chars:
        raise ContractError(f"{field} must be at most {max_chars} characters")
    return trimmed


def require_int(value: object, field: str, low: int, high: int) -> int:
    # bool is an int subclass; True would otherwise validate as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field} must be an integer")
    if not low <= value <= high:
        raise ContractError(f"{field} must be between {low} and {high}")
    return value


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    count: int = 5

    @classmethod
    def from_dict(cls, value: object) -> "SearchRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"query"}, "request", {"count"})
        return cls(
            query=require_string(data["query"], "request.query", MAX_QUERY_CHARS),
            count=require_int(data.get("count", 5), "request.count", 1, MAX_RESULTS),
        )


@dataclass(frozen=True, slots=True)
class FetchResultRequest:
    handle: str
    question: str

    @classmethod
    def from_dict(cls, value: object) -> "FetchResultRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"handle", "question"}, "request")
        return cls(
            handle=require_string(data["handle"], "request.handle", MAX_HANDLE_CHARS),
            question=require_string(data["question"], "request.question", MAX_QUESTION_CHARS),
        )


@dataclass(frozen=True, slots=True)
class FetchUrlRequest:
    """Only reachable when the deployment enables the literal-URL tool.

    Enabled, this is the one place the privileged model composes a destination,
    which is why it is optional and recommended off.
    """

    url: str
    question: str

    @classmethod
    def from_dict(cls, value: object) -> "FetchUrlRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"url", "question"}, "request")
        return cls(
            url=require_string(data["url"], "request.url", MAX_URL_CHARS),
            question=require_string(data["question"], "request.question", MAX_QUESTION_CHARS),
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What the model sees of a search hit: no URL, only an opaque handle."""

    handle: str
    title: str
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {"handle": self.handle, "title": self.title, "snippet": self.snippet}


@dataclass(frozen=True, slots=True)
class Distillation:
    """A fetched page after distillation and scrubbing. Carries no URL."""

    content: str
    title: str

    def to_dict(self) -> dict[str, object]:
        return {"content": self.content, "title": self.title}
