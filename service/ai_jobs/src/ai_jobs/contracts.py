"""Tool-neutral execution contracts.

These values deliberately contain no Kubernetes or worker configuration. Tool-specific
payload validation belongs to a reviewed tool definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ContractError(ValueError):
    """A value does not conform to a public or worker wire contract."""


class RunState(StrEnum):
    ACCEPTED = "accepted"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSAFE_SOURCE = "unsafe_source"
    INCOMPLETE_RESEARCH = "incomplete_research"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolFailure:
    code: FailureCode
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> "ToolFailure":
        data = require_object(value, "failure")
        require_exact_fields(data, {"code", "message"}, "failure")
        try:
            code = FailureCode(require_string(data["code"], "failure.code", 64))
        except ValueError as exc:
            raise ContractError("failure.code is not a public failure code") from exc
        return cls(code=code, message=require_string(data["message"], "failure.message", 500))


@dataclass(frozen=True, slots=True)
class BrokerBudgets:
    search_calls: int
    fetch_calls: int
    fetched_bytes: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: int = 90


def require_object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{path} must be an object")
    return value


def require_exact_fields(
    value: Mapping[str, Any], required: set[str], path: str, optional: set[str] | None = None
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ContractError(f"{path} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"{path} has unknown fields: {', '.join(sorted(unknown))}")


def require_string(value: object, path: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path} must be a string")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ContractError(f"{path} must not be empty")
    if len(cleaned) > maximum:
        raise ContractError(f"{path} must be at most {maximum} characters")
    return cleaned


def require_list(value: object, path: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    if len(value) > maximum:
        raise ContractError(f"{path} must contain at most {maximum} items")
    return value
