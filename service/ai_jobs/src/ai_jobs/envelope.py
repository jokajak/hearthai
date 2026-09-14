"""The reusable versioned tool-result envelope.

The Rust workers construct these; the control plane validates them before any
content reaches the model. Keeping a second implementation honest is the job of
``service/web_fetch/fixtures/manifest.json``, which both sides replay.

Nothing here knows about fetching. A future consumer supplies its own payload
validator and inherits the admission rules unchanged, rather than defining a
second answer to "when may content be released".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from ai_jobs.contracts import (
    ContractError,
    require_exact_fields,
    require_object,
    require_string,
)

ENVELOPE_VERSION = 1

ENVELOPE_FIELDS = {
    "envelope_version",
    "tool",
    "tool_version",
    "status",
    "trust",
    "inspection",
    "data",
    "error",
}


class EnvelopeStatus(StrEnum):
    OK = "ok"
    REJECTED = "rejected"
    ERROR = "error"


class Trust(StrEnum):
    """One value today. Fetched data never describes itself as anything else,
    and the marker grants no permission either way."""

    EXTERNAL_UNTRUSTED = "external_untrusted"


class InspectionStatus(StrEnum):
    NO_MATCH = "no_match"
    MATCH = "match"
    FAILED = "failed"
    NOT_RUN = "not_run"


class EnvelopeErrorCode(StrEnum):
    CONTENT_REJECTED = "content_rejected"
    INSPECTION_FAILED = "inspection_failed"
    UNSAFE_SOURCE = "unsafe_source"
    UNSUPPORTED_CONTENT = "unsupported_content"
    RESPONSE_LIMIT_EXCEEDED = "response_limit_exceeded"
    FETCH_FAILED = "fetch_failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"


#: Each code owns one message. A message composed from a body, a title, a URL,
#: matched text, or a worker exception would be a way for a withheld response to
#: reach model context through the error path.
FIXED_MESSAGES: Mapping[EnvelopeErrorCode, str] = {
    EnvelopeErrorCode.CONTENT_REJECTED: "Response content was withheld from model context.",
    EnvelopeErrorCode.INSPECTION_FAILED: "Response inspection did not complete.",
    EnvelopeErrorCode.UNSAFE_SOURCE: "The destination is not an allowed public web address.",
    EnvelopeErrorCode.UNSUPPORTED_CONTENT: "The response media type or encoding is not supported.",
    EnvelopeErrorCode.RESPONSE_LIMIT_EXCEEDED: "The response exceeded a configured size limit.",
    EnvelopeErrorCode.FETCH_FAILED: "The response could not be retrieved.",
    EnvelopeErrorCode.DEADLINE_EXCEEDED: "The fetch did not finish within its deadline.",
}


@dataclass(frozen=True, slots=True)
class EnvelopeError:
    code: EnvelopeErrorCode

    @property
    def message(self) -> str:
        return FIXED_MESSAGES[self.code]

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> "EnvelopeError":
        data = require_object(value, "envelope.error")
        require_exact_fields(data, {"code", "message"}, "envelope.error")
        try:
            code = EnvelopeErrorCode(require_string(data["code"], "envelope.error.code", 64))
        except ValueError as exc:
            raise ContractError("envelope.error.code is not a public failure code") from exc
        if require_string(data["message"], "envelope.error.message", 200) != FIXED_MESSAGES[code]:
            raise ContractError("envelope.error.message is not the fixed message for its code")
        return cls(code=code)


@dataclass(frozen=True, slots=True)
class Inspection:
    status: InspectionStatus
    #: A reference to the immutable policy the run was bound to internally, not
    #: an explanation and not a substitute for that binding.
    policy_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "policy_id": self.policy_id}

    @classmethod
    def from_dict(cls, value: object) -> "Inspection":
        data = require_object(value, "envelope.inspection")
        require_exact_fields(data, {"status", "policy_id"}, "envelope.inspection")
        try:
            status = InspectionStatus(require_string(data["status"], "envelope.inspection.status", 32))
        except ValueError as exc:
            raise ContractError("envelope.inspection.status is not a known inspection status") from exc
        policy_id = data["policy_id"]
        if policy_id is not None:
            policy_id = require_string(policy_id, "envelope.inspection.policy_id", 64)
            if not _is_policy_id(policy_id):
                raise ContractError("envelope.inspection.policy_id is not a policy identifier")
        return cls(status=status, policy_id=policy_id)


@dataclass(frozen=True, slots=True)
class ToolResultEnvelope:
    tool: str
    tool_version: int
    status: EnvelopeStatus
    trust: Trust
    inspection: Inspection
    data: Any | None
    error: EnvelopeError | None

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        tool: str,
        tool_version: int,
        validate_data: Callable[[object], Any],
    ) -> "ToolResultEnvelope":
        """Validates one envelope for one named tool version.

        The caller names the tool it expects, so an envelope for a different
        tool is a mismatch rather than something to be parsed leniently and
        checked later.
        """
        body = require_object(value, "envelope")
        require_exact_fields(body, ENVELOPE_FIELDS, "envelope")
        if body["envelope_version"] != ENVELOPE_VERSION:
            raise ContractError("envelope.envelope_version is not a supported envelope version")
        if require_string(body["tool"], "envelope.tool", 64) != tool:
            raise ContractError("envelope.tool does not identify the expected tool")
        if body["tool_version"] != tool_version:
            raise ContractError("envelope.tool_version is not a supported tool version")
        try:
            status = EnvelopeStatus(require_string(body["status"], "envelope.status", 32))
        except ValueError as exc:
            raise ContractError("envelope.status is not a known envelope status") from exc
        try:
            trust = Trust(require_string(body["trust"], "envelope.trust", 64))
        except ValueError as exc:
            raise ContractError("envelope.trust is not a known trust marker") from exc
        inspection = Inspection.from_dict(body["inspection"])
        data = None if body["data"] is None else validate_data(body["data"])
        error = None if body["error"] is None else EnvelopeError.from_dict(body["error"])
        envelope = cls(tool, tool_version, status, trust, inspection, data, error)
        envelope._check()
        return envelope

    def _check(self) -> None:
        """The admission rules, applied on the way in and on the way out."""
        if self.status is EnvelopeStatus.OK:
            if self.data is None:
                raise ContractError("envelope.data is required for a successful result")
            if self.error is not None:
                raise ContractError("envelope.error must be null for a successful result")
            if self.inspection.status is not InspectionStatus.NO_MATCH:
                raise ContractError(
                    "envelope.inspection.status must be a completed no_match for a successful result"
                )
            if self.inspection.policy_id is None:
                raise ContractError(
                    "envelope.inspection.policy_id must name the policy that admitted the content"
                )
            return
        if self.data is not None:
            raise ContractError("envelope.data must be null when no content is released")
        if self.error is None:
            raise ContractError("envelope.error is required when no content is released")
        rejected = self.error.code is EnvelopeErrorCode.CONTENT_REJECTED
        if (self.status is EnvelopeStatus.REJECTED) != rejected:
            raise ContractError("envelope.error.code does not match the envelope status")
        if rejected and self.inspection.status is not InspectionStatus.MATCH:
            raise ContractError("envelope.inspection.status must record the match that caused the rejection")

    def to_dict(self) -> dict[str, object]:
        return {
            "envelope_version": ENVELOPE_VERSION,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "status": self.status.value,
            "trust": self.trust.value,
            "inspection": self.inspection.to_dict(),
            "data": None if self.data is None else self.data.to_dict(),
            "error": None if self.error is None else self.error.to_dict(),
        }


def _is_policy_id(value: str) -> bool:
    return (
        2 <= len(value) <= 64
        and value[0].islower()
        and value[0].isascii()
        and all(character.islower() or character.isdigit() or character == "-" for character in value[1:])
        and value.isascii()
    )
