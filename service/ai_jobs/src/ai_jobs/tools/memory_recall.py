"""``memory_recall`` v1: read entries from the household's shared memory store.

Model-callable. The request is a query and a limit, nothing else; which store, which service
and which credential are the profile's, never the request's. The result is a ``household_shared``
envelope: written by household members, not external, still not instructions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    ToolFailure,
    require_exact_fields,
    require_object,
    require_string,
)
from ai_jobs.envelope import (
    RECALL_MAX_ENTRIES,
    RECALL_MAX_QUERY_CHARS,
    Envelope,
    SharedTrust,
    validate_recall_data,
)

TOOL_KEY = "memory_recall"
TOOL_VERSION = 1
WORKER_PROFILE = "memory-recall-v1"

MAX_QUERY_CHARS = RECALL_MAX_QUERY_CHARS
MIN_LIMIT = 1
MAX_LIMIT = RECALL_MAX_ENTRIES  # a request never asks for more than a result may hold
DEFAULT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class MemoryRecallRequest:
    query: str
    limit: int = DEFAULT_LIMIT

    @classmethod
    def from_dict(cls, value: object) -> "MemoryRecallRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"query"}, "request", optional={"limit"})
        query = require_string(data["query"], "request.query", MAX_QUERY_CHARS)
        limit = data.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ContractError("request.limit must be an integer")
        if not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise ContractError(f"request.limit must be between {MIN_LIMIT} and {MAX_LIMIT}")
        return cls(query=query, limit=limit)

    def to_dict(self) -> dict[str, object]:
        return {"query": self.query, "limit": self.limit}


class MemoryRecallDefinition:
    key = TOOL_KEY
    version = TOOL_VERSION
    worker_profile = WORKER_PROFILE

    def validate_request(self, value: object) -> MemoryRecallRequest:
        return MemoryRecallRequest.from_dict(value)

    def validate_result(self, value: object) -> Envelope:
        return Envelope.from_dict(
            value,
            tool=TOOL_KEY,
            tool_version=TOOL_VERSION,
            payload=validate_recall_data,
            trust=SharedTrust.HOUSEHOLD_SHARED,
        )

    def grants_for(self, request: object) -> frozenset[str]:
        return frozenset({"household_memory_read"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        return BrokerBudgets(0, 0, 0, 0, 0, 15)

    def storage_view(self, request: object) -> dict[str, object]:
        """The query's digest and the limit. The query itself is never kept."""
        assert isinstance(request, MemoryRecallRequest)
        return {
            "query_digest": hashlib.sha256(request.query.encode("utf-8")).hexdigest(),
            "limit": request.limit,
        }

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        return failure
