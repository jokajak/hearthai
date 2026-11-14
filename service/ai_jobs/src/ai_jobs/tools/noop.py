"""Internal ``noop`` tool: proves the executor path end to end, does nothing else.

It is deliberately not model-callable: it is never in the registry the HTTP
surface exposes, and its request has no fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    ToolFailure,
    require_exact_fields,
    require_object,
)
from ai_jobs.envelope import Envelope

TOOL_KEY = "noop"
TOOL_VERSION = 1
WORKER_PROFILE = "noop-v1"


def _validate_payload(value: object) -> dict[str, object]:
    data = require_object(value, "data")
    require_exact_fields(data, {"ok"}, "data")
    if data["ok"] is not True:
        raise ContractError("data.ok must be true")
    return dict(data)


@dataclass(frozen=True, slots=True)
class NoopRequest:
    def to_dict(self) -> dict[str, object]:
        return {}


class NoopDefinition:
    key = TOOL_KEY
    version = TOOL_VERSION
    worker_profile = WORKER_PROFILE

    def validate_request(self, value: object) -> NoopRequest:
        data = require_object(value, "request")
        require_exact_fields(data, set(), "request")
        return NoopRequest()

    def validate_result(self, value: object) -> Envelope:
        return Envelope.from_dict(value, tool=TOOL_KEY, tool_version=TOOL_VERSION, payload=_validate_payload)

    def grants_for(self, request: object) -> frozenset[str]:
        return frozenset()

    def budgets_for(self, request: object) -> BrokerBudgets:
        return BrokerBudgets(0, 0, 0, 0, 0, 10)

    def storage_view(self, request: object) -> dict[str, object]:
        return {}

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        return failure
