"""Fixed GitHub PR worker profile contract.

The model supplies intent and an explicitly authorised repository reference;
it never supplies a command, image, credential, branch name, or publish target.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    ToolFailure,
    require_exact_fields,
    require_object,
    require_string,
)

MAX_REPOSITORY_CHARS = 200
MAX_INSTRUCTION_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class GitHubPRRequest:
    repository: str
    instruction: str

    @classmethod
    def from_dict(cls, value: object) -> "GitHubPRRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"repository", "instruction"}, "request")
        repository = require_string(
            data["repository"], "request.repository", MAX_REPOSITORY_CHARS
        )
        if repository.count("/") != 1 or any(part == "" for part in repository.split("/")):
            raise ContractError("request.repository must be owner/name")
        return cls(
            repository,
            require_string(data["instruction"], "request.instruction", MAX_INSTRUCTION_CHARS),
        )

    def to_dict(self) -> dict[str, str]:
        return {"repository": self.repository, "instruction": self.instruction}


class GitHubPRDefinition:
    key = "github_pr"
    version = 1
    worker_profile = "github-pr-v1"

    def validate_request(self, value: object) -> GitHubPRRequest:
        return GitHubPRRequest.from_dict(value)

    def validate_result(self, value: object) -> object:
        # Publisher results are defined by the publisher API, not trusted from
        # worker output. This keeps the registry closed while that protocol lands.
        return value

    def grants_for(self, request: object) -> frozenset[str]:
        if not isinstance(request, GitHubPRRequest):
            raise TypeError("expected GitHubPRRequest")
        return frozenset({"repository_materialize", "repository_publish"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        if not isinstance(request, GitHubPRRequest):
            raise TypeError("expected GitHubPRRequest")
        return BrokerBudgets(0, 0, 0, 0, 0, elapsed_seconds=900)

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        return failure
