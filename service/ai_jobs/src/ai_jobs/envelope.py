"""The reusable tool-result envelope, version 1.

The Rust stages build these; the control plane validates them before anything
reaches a caller, and a future consumer validates them before admitting content
to model context. Both sides enforce the same rules, and the shared fixtures in
``service/web_fetch/fixtures`` are what keeps them agreeing.

Two invariants are worth stating plainly, because they are the reason the
envelope exists rather than a bare payload:

* Control fields are server-owned. Source text that looks like an envelope is
  still source text; it arrives inside ``data`` and cannot set ``status``,
  ``trust``, or the tool identity of the envelope carrying it.
* A failure carries no remote text. Each code has exactly one message, and a
  value whose message is anything else is not a valid envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from ai_jobs.contracts import (
    ContractError,
    require_exact_fields,
    require_list,
    require_object,
    require_string,
)

ENVELOPE_VERSION = 1


class Status(StrEnum):
    OK = "ok"
    REJECTED = "rejected"
    ERROR = "error"


class Trust(StrEnum):
    """Describes fetched data. It grants nothing."""

    EXTERNAL_UNTRUSTED = "external_untrusted"


class SharedTrust(StrEnum):
    """Trust values for data the household wrote itself. It grants nothing either.

    Kept apart from :class:`Trust` because ``Trust`` mirrors the shared envelope schema in
    ``service/web_fetch/schemas``, which the Rust side also reads. An envelope carries exactly
    the one trust value its tool declares (see ``Envelope.from_dict``), so a web fetch can
    never claim this one.
    """

    #: Written by household members: not external, and still not instructions.
    HOUSEHOLD_SHARED = "household_shared"


class InspectionStatus(StrEnum):
    NO_MATCH = "no_match"
    MATCH = "match"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ErrorCode(StrEnum):
    CONTENT_REJECTED = "content_rejected"
    INSPECTION_FAILED = "inspection_failed"
    UNSAFE_SOURCE = "unsafe_source"
    UNSUPPORTED_CONTENT = "unsupported_content"
    RESPONSE_LIMIT_EXCEEDED = "response_limit_exceeded"
    FETCH_FAILED = "fetch_failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INTERNAL_ERROR = "internal_error"


#: The one message each code may carry. Mirrors ``ErrorCode::message`` in the
#: Rust contracts crate.
ERROR_MESSAGES: Mapping[ErrorCode, str] = {
    ErrorCode.CONTENT_REJECTED: "Response content was withheld from model context.",
    ErrorCode.INSPECTION_FAILED: "Response inspection did not complete; no content was returned.",
    ErrorCode.UNSAFE_SOURCE: "The requested destination is not allowed.",
    ErrorCode.UNSUPPORTED_CONTENT: "The response media type or encoding is not supported.",
    ErrorCode.RESPONSE_LIMIT_EXCEEDED: "The response exceeded a configured limit.",
    ErrorCode.FETCH_FAILED: "The request to the source could not be completed.",
    ErrorCode.DEADLINE_EXCEEDED: "The request did not complete within its deadline.",
    ErrorCode.INTERNAL_ERROR: "The tool could not complete this request.",
}

#: ``household_shared`` data is read from the household's own store, not inspected, so a
#: successful one reports ``not_run`` with no policy, and only these failure codes apply.
_HOUSEHOLD_CODES = frozenset(
    {
        ErrorCode.RESPONSE_LIMIT_EXCEEDED,
        ErrorCode.FETCH_FAILED,
        ErrorCode.DEADLINE_EXCEEDED,
        ErrorCode.INTERNAL_ERROR,
    }
)

#: Limits for the ``household_shared`` recall payload. The worker enforces them when it builds
#: the data and ``validate_recall_data`` enforces them again on the control plane.
RECALL_MAX_QUERY_CHARS = 500
RECALL_MAX_ENTRIES = 10
RECALL_MAX_CONTENT_CHARS = 4_000
RECALL_MAX_TOTAL_CONTENT_CHARS = 12_000
RECALL_MAX_AUTHOR_CHARS = 100
RECALL_MAX_CREATED_AT_CHARS = 40
RECALL_MAX_TAGS = 10
RECALL_MAX_TAG_CHARS = 50

#: Only ``content_rejected`` is a rejection; everything else is a tool error.
_REJECTION_CODES = frozenset({ErrorCode.CONTENT_REJECTED})

#: Inspection statuses each code may be reported with. ``no_match`` appears
#: nowhere: a completed clean inspection is only ever reported with content, so
#: no failure can claim the response was checked and found clean.
_PERMITTED_INSPECTION: Mapping[ErrorCode, frozenset[InspectionStatus]] = {
    ErrorCode.CONTENT_REJECTED: frozenset({InspectionStatus.MATCH}),
    ErrorCode.INSPECTION_FAILED: frozenset({InspectionStatus.FAILED}),
}
_DEFAULT_PERMITTED_INSPECTION = frozenset({InspectionStatus.NOT_RUN, InspectionStatus.FAILED})


def status_for(code: ErrorCode) -> Status:
    return Status.REJECTED if code in _REJECTION_CODES else Status.ERROR


@dataclass(frozen=True, slots=True)
class Inspection:
    status: InspectionStatus
    policy_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "policy_id": self.policy_id}

    @classmethod
    def from_dict(cls, value: object) -> "Inspection":
        data = require_object(value, "inspection")
        require_exact_fields(data, {"status", "policy_id"}, "inspection")
        try:
            status = InspectionStatus(require_string(data["status"], "inspection.status", 32))
        except ValueError as exc:
            raise ContractError("inspection.status is not a known inspection status") from exc
        policy_id = data["policy_id"]
        if policy_id is not None:
            policy_id = require_string(policy_id, "inspection.policy_id", 64)
            if not _is_policy_id(policy_id):
                raise ContractError("inspection.policy_id is not a valid policy identifier")
        return cls(status=status, policy_id=policy_id)


@dataclass(frozen=True, slots=True)
class EnvelopeError:
    code: ErrorCode
    message: str

    @classmethod
    def for_code(cls, code: ErrorCode) -> "EnvelopeError":
        """The only way to build one: the message comes from the code."""
        return cls(code=code, message=ERROR_MESSAGES[code])

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> "EnvelopeError":
        data = require_object(value, "error")
        require_exact_fields(data, {"code", "message"}, "error")
        try:
            code = ErrorCode(require_string(data["code"], "error.code", 64))
        except ValueError as exc:
            raise ContractError("error.code is not a public error code") from exc
        message = require_string(data["message"], "error.message", 500)
        if message != ERROR_MESSAGES[code]:
            raise ContractError("error.message is not this code's fixed message")
        return cls(code=code, message=message)


#: Validates a tool's payload. Raises ContractError if the payload is not valid
#: for that tool and version.
PayloadValidator = Callable[[object], Any]


@dataclass(frozen=True, slots=True)
class Envelope:
    envelope_version: int
    tool: str
    tool_version: int
    status: Status
    trust: Trust | SharedTrust
    inspection: Inspection
    data: Any | None
    error: EnvelopeError | None

    @classmethod
    def ok(cls, tool: str, tool_version: int, data: Any, policy_id: str) -> "Envelope":
        return cls(
            envelope_version=ENVELOPE_VERSION,
            tool=tool,
            tool_version=tool_version,
            status=Status.OK,
            trust=Trust.EXTERNAL_UNTRUSTED,
            inspection=Inspection(InspectionStatus.NO_MATCH, policy_id),
            data=data,
            error=None,
        )

    @classmethod
    def ok_household(cls, tool: str, tool_version: int, data: Any) -> "Envelope":
        """A successful ``household_shared`` result: read from the household store, not inspected."""
        envelope = cls(
            envelope_version=ENVELOPE_VERSION,
            tool=tool,
            tool_version=tool_version,
            status=Status.OK,
            trust=SharedTrust.HOUSEHOLD_SHARED,
            inspection=Inspection(InspectionStatus.NOT_RUN, None),
            data=data,
            error=None,
        )
        _check(envelope, None)
        return envelope

    @classmethod
    def failure(
        cls,
        tool: str,
        tool_version: int,
        code: ErrorCode,
        inspection: Inspection,
        trust: Trust | SharedTrust = Trust.EXTERNAL_UNTRUSTED,
    ) -> "Envelope":
        envelope = cls(
            envelope_version=ENVELOPE_VERSION,
            tool=tool,
            tool_version=tool_version,
            status=status_for(code),
            trust=trust,
            inspection=inspection,
            data=None,
            error=EnvelopeError.for_code(code),
        )
        _check(envelope, None)
        return envelope

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        tool: str,
        tool_version: int,
        payload: PayloadValidator | None = None,
        trust: Trust | SharedTrust = Trust.EXTERNAL_UNTRUSTED,
    ) -> "Envelope":
        """Parse and validate an envelope for one named tool and version.

        ``trust`` is the one trust value that tool's envelopes carry; any other is refused.
        """
        data = require_object(value, "envelope")
        require_exact_fields(
            data,
            {"envelope_version", "tool", "tool_version", "status", "trust", "inspection", "data", "error"},
            "envelope",
        )
        version = data["envelope_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise ContractError("envelope.envelope_version must be an integer")
        if version != ENVELOPE_VERSION:
            raise ContractError(f"unsupported envelope_version: {version}")
        tool_key = require_string(data["tool"], "envelope.tool", 64)
        carried_version = data["tool_version"]
        if isinstance(carried_version, bool) or not isinstance(carried_version, int):
            raise ContractError("envelope.tool_version must be an integer")
        if tool_key != tool or carried_version != tool_version:
            raise ContractError(
                f"envelope carries {tool_key}:v{carried_version}, expected {tool}:v{tool_version}"
            )
        try:
            status = Status(require_string(data["status"], "envelope.status", 32))
            carried_trust = _parse_trust(require_string(data["trust"], "envelope.trust", 32))
        except ValueError as exc:
            raise ContractError("envelope status or trust is not a known value") from exc
        if carried_trust is not trust:
            raise ContractError(f"envelope trust is not {trust.value}")
        inspection = Inspection.from_dict(data["inspection"])
        error = None if data["error"] is None else EnvelopeError.from_dict(data["error"])
        envelope = cls(
            envelope_version=version,
            tool=tool_key,
            tool_version=carried_version,
            status=status,
            trust=carried_trust,
            inspection=inspection,
            data=data["data"],
            error=error,
        )
        _check(envelope, payload)
        return envelope

    def to_dict(self) -> dict[str, object]:
        data = self.data
        if data is not None and hasattr(data, "to_dict"):
            data = data.to_dict()
        return {
            "envelope_version": self.envelope_version,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "status": self.status.value,
            "trust": self.trust.value,
            "inspection": self.inspection.to_dict(),
            "data": data,
            "error": None if self.error is None else self.error.to_dict(),
        }


def _parse_trust(value: str) -> Trust | SharedTrust:
    for family in (Trust, SharedTrust):
        try:
            return family(value)
        except ValueError:
            continue
    raise ValueError(value)


def _check(envelope: Envelope, payload: PayloadValidator | None) -> None:
    household = envelope.trust is SharedTrust.HOUSEHOLD_SHARED
    if household:
        if envelope.inspection.status not in (InspectionStatus.NOT_RUN, InspectionStatus.FAILED) or (
            envelope.inspection.policy_id is not None
        ):
            raise ContractError("household_shared data is not inspected: not_run with no policy")
        if envelope.error is not None and envelope.error.code not in _HOUSEHOLD_CODES:
            raise ContractError(f"{envelope.error.code.value} cannot accompany household_shared data")
    if envelope.status is Status.OK:
        if envelope.data is None or envelope.error is not None:
            raise ContractError("ok requires non-null data and null error")
        if household:
            if envelope.inspection.status is not InspectionStatus.NOT_RUN:
                raise ContractError("household_shared data reports an inspection that did not run")
            if payload is not None:
                payload(envelope.data)
            return
        if envelope.inspection.status is not InspectionStatus.NO_MATCH:
            raise ContractError("ok requires a completed no_match inspection")
        if envelope.inspection.policy_id is None:
            raise ContractError("ok requires the policy that cleared the response")
        if payload is not None:
            payload(envelope.data)
        return

    if envelope.data is not None or envelope.error is None:
        raise ContractError("a failure requires null data and a fixed error")
    if status_for(envelope.error.code) is not envelope.status:
        raise ContractError(f"{envelope.status.value} is not the status for {envelope.error.code.value}")
    permitted = _PERMITTED_INSPECTION.get(envelope.error.code, _DEFAULT_PERMITTED_INSPECTION)
    if envelope.inspection.status not in permitted:
        raise ContractError(
            f"inspection status {envelope.inspection.status.value} cannot accompany {envelope.error.code.value}"
        )
    if envelope.inspection.status is InspectionStatus.MATCH and envelope.inspection.policy_id is None:
        raise ContractError("a match must name the policy that matched")


def _is_policy_id(value: str) -> bool:
    """Server-owned identifier, ASCII only - the same rule the Rust side applies."""
    return bool(value) and all(
        ("a" <= character <= "z") or ("0" <= character <= "9") or character in "-_." for character in value
    )


def validate_recall_data(value: object) -> dict[str, object]:
    """The ``household_shared`` recall payload: at most ten entries, each with four fields.

    Size limits are per entry and in total, so a store full of long notes cannot turn one
    recall into an unbounded block of model context.
    """
    data = require_object(value, "data")
    require_exact_fields(data, {"entries"}, "data")
    entries = require_list(data["entries"], "data.entries", RECALL_MAX_ENTRIES)
    total = 0
    for index, item in enumerate(entries):
        path = f"data.entries[{index}]"
        entry = require_object(item, path)
        require_exact_fields(entry, {"content", "author", "created_at", "tags"}, path)
        content = require_string(entry["content"], f"{path}.content", RECALL_MAX_CONTENT_CHARS)
        total += len(content)
        require_string(entry["author"], f"{path}.author", RECALL_MAX_AUTHOR_CHARS, allow_empty=True)
        require_string(entry["created_at"], f"{path}.created_at", RECALL_MAX_CREATED_AT_CHARS, allow_empty=True)
        for tag in require_list(entry["tags"], f"{path}.tags", RECALL_MAX_TAGS):
            require_string(tag, f"{path}.tags[]", RECALL_MAX_TAG_CHARS)
    if total > RECALL_MAX_TOTAL_CONTENT_CHARS:
        raise ContractError(f"data.entries content must total at most {RECALL_MAX_TOTAL_CONTENT_CHARS} characters")
    return dict(data)
