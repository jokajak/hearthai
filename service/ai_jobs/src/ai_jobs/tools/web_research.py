"""Versioned, model-facing web research contract and policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from ai_jobs.contracts import (
    BrokerBudgets,
    ContractError,
    ToolFailure,
    require_exact_fields,
    require_list,
    require_object,
    require_string,
)

MAX_QUESTION_CHARS = 8_000
MAX_SOURCES = 12
MAX_FINDINGS = 20
Depth = Literal["quick", "standard"]


@dataclass(frozen=True, slots=True)
class WebResearchRequest:
    question: str
    depth: Depth = "standard"
    source_limit: int = 8

    @classmethod
    def from_dict(cls, value: object) -> "WebResearchRequest":
        data = require_object(value, "request")
        require_exact_fields(data, {"question"}, "request", {"depth", "source_limit"})
        question = require_string(data["question"], "request.question", MAX_QUESTION_CHARS)
        depth = data.get("depth", "standard")
        if depth not in ("quick", "standard"):
            raise ContractError("request.depth must be quick or standard")
        default_limit = 4 if depth == "quick" else 8
        source_limit = data.get("source_limit", default_limit)
        if isinstance(source_limit, bool) or not isinstance(source_limit, int):
            raise ContractError("request.source_limit must be an integer")
        if not 1 <= source_limit <= MAX_SOURCES:
            raise ContractError(f"request.source_limit must be between 1 and {MAX_SOURCES}")
        return cls(question=question, depth=depth, source_limit=source_limit)

    def to_dict(self) -> dict[str, object]:
        return {"question": self.question, "depth": self.depth, "source_limit": self.source_limit}


def budgets_for(request: WebResearchRequest) -> BrokerBudgets:
    if request.depth == "quick":
        return BrokerBudgets(3, min(request.source_limit, 4), 2_000_000, 16_000, 4_000)
    return BrokerBudgets(6, request.source_limit, 6_000_000, 48_000, 8_000)


@dataclass(frozen=True, slots=True)
class Evidence:
    source_id: str
    support: str

    @classmethod
    def from_dict(cls, value: object, path: str) -> "Evidence":
        data = require_object(value, path)
        require_exact_fields(data, {"source_id", "support"}, path)
        return cls(
            require_string(data["source_id"], f"{path}.source_id", 64),
            require_string(data["support"], f"{path}.support", 2_000),
        )

    def to_dict(self) -> dict[str, str]:
        return {"source_id": self.source_id, "support": self.support}


@dataclass(frozen=True, slots=True)
class Finding:
    statement: str
    evidence: tuple[Evidence, ...]
    confidence: Literal["low", "medium", "high"]

    @classmethod
    def from_dict(cls, value: object, path: str) -> "Finding":
        data = require_object(value, path)
        require_exact_fields(data, {"statement", "evidence", "confidence"}, path)
        evidence = tuple(
            Evidence.from_dict(item, f"{path}.evidence[{index}]")
            for index, item in enumerate(require_list(data["evidence"], f"{path}.evidence", 12))
        )
        if not evidence:
            raise ContractError(f"{path}.evidence must not be empty")
        confidence = data["confidence"]
        if confidence not in ("low", "medium", "high"):
            raise ContractError(f"{path}.confidence must be low, medium, or high")
        return cls(require_string(data["statement"], f"{path}.statement", 4_000), evidence, confidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "statement": self.statement,
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    url: str
    title: str
    retrieved_at: str

    @classmethod
    def from_dict(cls, value: object, path: str) -> "Source":
        data = require_object(value, path)
        require_exact_fields(data, {"id", "url", "title", "retrieved_at"}, path)
        source_id = require_string(data["id"], f"{path}.id", 64)
        url = require_string(data["url"], f"{path}.url", 2_048)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ContractError(f"{path}.url must be an HTTP(S) URL without userinfo")
        retrieved_at = require_string(data["retrieved_at"], f"{path}.retrieved_at", 40)
        try:
            datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"{path}.retrieved_at must be an ISO 8601 timestamp") from exc
        return cls(source_id, url, require_string(data["title"], f"{path}.title", 500), retrieved_at)

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "url": self.url, "title": self.title, "retrieved_at": self.retrieved_at}


@dataclass(frozen=True, slots=True)
class WebResearchResult:
    summary: str
    findings: tuple[Finding, ...]
    sources: tuple[Source, ...]
    conflicts: tuple[str, ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> "WebResearchResult":
        data = require_object(value, "result")
        require_exact_fields(data, {"summary", "findings", "sources", "conflicts", "limitations"}, "result")
        findings = tuple(
            Finding.from_dict(item, f"result.findings[{index}]")
            for index, item in enumerate(require_list(data["findings"], "result.findings", MAX_FINDINGS))
        )
        sources = tuple(
            Source.from_dict(item, f"result.sources[{index}]")
            for index, item in enumerate(require_list(data["sources"], "result.sources", MAX_SOURCES))
        )
        source_ids = [source.id for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ContractError("result.sources contains duplicate IDs")
        known = set(source_ids)
        referenced = {evidence.source_id for finding in findings for evidence in finding.evidence}
        missing = referenced - known
        if missing:
            raise ContractError(f"result evidence references unknown sources: {', '.join(sorted(missing))}")
        conflicts = _string_array(data["conflicts"], "result.conflicts")
        limitations = _string_array(data["limitations"], "result.limitations")
        return cls(require_string(data["summary"], "result.summary", 12_000), findings, sources, conflicts, limitations)

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
            "sources": [item.to_dict() for item in self.sources],
            "conflicts": list(self.conflicts),
            "limitations": list(self.limitations),
        }


def _string_array(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        require_string(item, f"{path}[{index}]", 2_000)
        for index, item in enumerate(require_list(value, path, 20))
    )


class WebResearchDefinition:
    key = "web_research"
    version = 1
    worker_profile = "web-research-v1"

    def validate_request(self, value: object) -> WebResearchRequest:
        return WebResearchRequest.from_dict(value)

    def validate_result(self, value: object) -> WebResearchResult:
        return WebResearchResult.from_dict(value)

    def grants_for(self, request: object) -> frozenset[str]:
        if not isinstance(request, WebResearchRequest):
            raise TypeError("expected WebResearchRequest")
        return frozenset({"search", "fetch", "infer"})

    def budgets_for(self, request: object) -> BrokerBudgets:
        if not isinstance(request, WebResearchRequest):
            raise TypeError("expected WebResearchRequest")
        return budgets_for(request)

    def public_failure(self, failure: ToolFailure) -> ToolFailure:
        return failure
