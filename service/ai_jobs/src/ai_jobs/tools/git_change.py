"""Generic Git change worker profile contract.

The model selects an opaque HearthAI-authorised repository ID and supplies work
intent. ai-jobs resolves that ID to a vetted Git remote and provider adapter.
No model-facing field contains a URL, command, credential, branch, or target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    ToolFailure,
    require_exact_fields,
    require_object,
    require_string,
)

_REPOSITORY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_INSTRUCTION_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class GitChangeRequest:
    repository_id: str
    instruction: str

    @classmethod
    def from_dict(cls, value: object) -> "GitChangeRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"repository_id", "instruction"}, "request")
        repository_id = require_string(
            data["repository_id"], "request.repository_id", 128
        )
        if not _REPOSITORY_ID.fullmatch(repository_id):
            raise ContractError("request.repository_id is not an authorised ID")
        return cls(
            repository_id,
            require_string(data["instruction"], "request.instruction", MAX_INSTRUCTION_CHARS),
        )

    def to_dict(self) -> dict[str, str]:
        return {"repository_id": self.repository_id, "instruction": self.instruction}


class GitChangeDefinition:
    key = "git_change"
    version = 1
    worker_profile = "git-change-v1"

    def validate_request(self, value: object) -> GitChangeRequest:
        return GitChangeRequest.from_dict(value)

    def validate_result(self, value: object) -> object:
        raise NotImplementedError("git_change results land with the publisher protocol")

    def grants_for(self, request: object) -> frozenset[str]:
        if not isinstance(request, GitChangeRequest):
            raise TypeError("expected GitChangeRequest")
        return frozenset({"repository_materialize", "repository_publish"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        if not isinstance(request, GitChangeRequest):
            raise TypeError("expected GitChangeRequest")
        return BrokerBudgets(
            search_calls=0,
            fetch_calls=0,
            fetched_bytes=0,
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=900,
        )

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        return failure
