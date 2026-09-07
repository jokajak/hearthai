import json
from pathlib import Path

import pytest

from ai_jobs.contracts import BrokerBudgets, ContractError, FailureCode, RunState, ToolFailure
from ai_jobs.tools.web_research import WebResearchRequest, WebResearchResult, budgets_for

FIXTURES = Path(__file__).parent / "fixtures" / "web_research"
OPENAPI = Path(__file__).parents[1] / "openapi.json"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_minimal_request_applies_server_defaults():
    request = WebResearchRequest.from_dict(fixture("valid-request-minimal.json"))
    assert request.depth == "standard"
    assert request.source_limit == 8
    assert WebResearchRequest.from_dict(request.to_dict()) == request


def test_maximal_request_is_valid():
    request = WebResearchRequest.from_dict(fixture("valid-request-maximal.json"))
    assert request.source_limit == 12


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "missing"),
        ({"question": "  "}, "empty"),
        ({"question": "x" * 8001}, "8000"),
        ({"question": "q", "depth": "unlimited"}, "quick or standard"),
        ({"question": "q", "source_limit": 0}, "between 1 and 12"),
        ({"question": "q", "source_limit": True}, "integer"),
    ],
)
def test_request_bounds_fail_closed(payload, message):
    with pytest.raises(ContractError, match=message):
        WebResearchRequest.from_dict(payload)


def test_infrastructure_fields_are_rejected():
    with pytest.raises(ContractError, match="command, image"):
        WebResearchRequest.from_dict(fixture("invalid-request-infrastructure.json"))


def test_result_round_trip_and_evidence_integrity():
    result = WebResearchResult.from_dict(fixture("valid-result.json"))
    assert result.findings[0].evidence[0].source_id == "s1"
    assert WebResearchResult.from_dict(result.to_dict()) == result


def test_result_rejects_unknown_evidence_source():
    with pytest.raises(ContractError, match="unknown sources: missing"):
        WebResearchResult.from_dict(fixture("invalid-result-missing-source.json"))


def test_result_rejects_duplicate_source_ids():
    payload = fixture("valid-result.json")
    payload["sources"].append(payload["sources"][0])
    with pytest.raises(ContractError, match="duplicate IDs"):
        WebResearchResult.from_dict(payload)


def test_result_rejects_non_http_and_credentialed_urls():
    for url in ("file:///etc/passwd", "https://user:pass@example.org/"):
        payload = fixture("valid-result.json")
        payload["sources"][0]["url"] = url
        with pytest.raises(ContractError, match=r"HTTP\(S\)"):
            WebResearchResult.from_dict(payload)


def test_depth_resolves_broker_budgets_not_worker_deadline():
    quick = budgets_for(WebResearchRequest.from_dict({"question": "q", "depth": "quick", "source_limit": 2}))
    standard = budgets_for(WebResearchRequest.from_dict({"question": "q", "depth": "standard", "source_limit": 12}))
    assert quick == BrokerBudgets(3, 2, 2_000_000, 16_000, 4_000)
    assert standard == BrokerBudgets(6, 12, 6_000_000, 48_000, 8_000)
    assert quick.elapsed_seconds == standard.elapsed_seconds == 90


def test_all_states_and_public_failures_round_trip():
    assert {state.value for state in RunState} == {
        "accepted", "starting", "running", "succeeded", "failed", "timed_out", "cancelled"
    }
    for code in FailureCode:
        failure = ToolFailure(code, "safe message")
        assert ToolFailure.from_dict(failure.to_dict()) == failure


def test_failure_rejects_internal_details():
    with pytest.raises(ContractError, match="unknown fields"):
        ToolFailure.from_dict({"code": "internal_error", "message": "failed", "pod": "worker-123"})


def test_public_openapi_has_only_specific_tool_routes_and_idempotency_header():
    document = json.loads(OPENAPI.read_text())
    assert set(document["paths"]) == {"/healthz", "/readyz", "/v1/tools/web-research"}
    operation = document["paths"]["/v1/tools/web-research"]["post"]
    assert operation["operationId"] == "webResearch"
    assert operation["parameters"] == [{
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "format": "uuid"},
        "description": "Adapter-generated identity for one logical invocation.",
    }]


def test_public_request_schema_has_no_runtime_controls():
    document = json.loads(OPENAPI.read_text())
    request_schema = json.dumps(document["components"]["schemas"]["WebResearchRequest"]).lower()
    forbidden = {
        "image", "command", "pod", "namespace", "environment", "credential", "mount", "workspace", "tool"
    }
    assert not {term for term in forbidden if term in request_schema}
