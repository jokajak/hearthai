"""The memory_recall v1 definition, the household_shared envelope, the profile and the OpenAPI document."""

import hashlib
import json
from pathlib import Path

import pytest

from ai_jobs.contracts import ContractError, FailureCode, ToolFailure
from ai_jobs.envelope import (
    RECALL_MAX_CONTENT_CHARS,
    RECALL_MAX_ENTRIES,
    RECALL_MAX_TAGS,
    RECALL_MAX_TOTAL_CONTENT_CHARS,
    Envelope,
    ErrorCode,
    Inspection,
    InspectionStatus,
    SharedTrust,
    Trust,
    validate_recall_data,
)
from ai_jobs.openapi import openapi_document
from ai_jobs.profiles import memory_recall_profile
from ai_jobs.registry import ToolRegistry
from ai_jobs.tools.memory_recall import MemoryRecallDefinition, MemoryRecallRequest
from ai_jobs.tools.web_fetch import TOOL_KEY as WEB_FETCH_KEY

TOOL = MemoryRecallDefinition()


def entry(content="a note", **changes):
    value = {"content": content, "author": "sam", "created_at": "2026-11-14T12:00:00+00:00", "tags": ["a"]}
    value.update(changes)
    return value


def household_ok(entries):
    return Envelope.ok_household("memory_recall", 1, {"entries": entries}).to_dict()


# ---- request ---------------------------------------------------------------


def test_limit_defaults_to_five_and_the_request_round_trips():
    request = TOOL.validate_request({"query": "wifi password hint"})
    assert request == MemoryRecallRequest("wifi password hint", 5)
    assert MemoryRecallRequest.from_dict(request.to_dict()) == request


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "missing: query"),
        ({"query": "  "}, "empty"),
        ({"query": "x" * 501}, "500"),
        ({"query": 3}, "string"),
        ({"query": "q", "limit": 0}, "between 1 and 10"),
        ({"query": "q", "limit": 11}, "between 1 and 10"),
        ({"query": "q", "limit": True}, "integer"),
        ({"query": "q", "limit": 2.5}, "integer"),
        ({"query": "q", "store": "other"}, "unknown fields: store"),
        ({"query": "q", "token": "x"}, "unknown fields: token"),
        ([], "object"),
    ],
)
def test_request_bounds_fail_closed(payload, message):
    with pytest.raises(ContractError, match=message):
        TOOL.validate_request(payload)


def test_the_boundary_values_are_accepted():
    assert TOOL.validate_request({"query": "x" * 500, "limit": 10}).limit == 10
    assert TOOL.validate_request({"query": "q", "limit": 1}).limit == 1


def test_storage_view_is_the_query_digest_and_limit_only():
    request = TOOL.validate_request({"query": "the boiler code", "limit": 3})
    view = TOOL.storage_view(request)
    assert view == {"query_digest": hashlib.sha256(b"the boiler code").hexdigest(), "limit": 3}
    assert "boiler" not in json.dumps(view)


def test_the_tool_is_in_a_registry_under_its_profile():
    assert ToolRegistry((TOOL,)).resolve("memory_recall", 1).worker_profile == "memory-recall-v1"


# ---- household_shared envelope -----------------------------------------------


def test_a_household_envelope_round_trips_through_the_tool_validator():
    envelope = TOOL.validate_result(household_ok([entry(), entry("another", tags=[])]))
    assert envelope.trust is SharedTrust.HOUSEHOLD_SHARED
    assert envelope.inspection == Inspection(InspectionStatus.NOT_RUN, None)
    assert len(envelope.data["entries"]) == 2


def test_an_empty_recall_is_a_valid_result():
    assert TOOL.validate_result(household_ok([])).data == {"entries": []}


def test_household_failures_are_fixed_and_validate():
    for code in (ErrorCode.FETCH_FAILED, ErrorCode.DEADLINE_EXCEEDED, ErrorCode.INTERNAL_ERROR):
        failure = Envelope.failure(
            "memory_recall", 1, code, Inspection(InspectionStatus.NOT_RUN, None), trust=SharedTrust.HOUSEHOLD_SHARED
        ).to_dict()
        assert TOOL.validate_result(failure).error.code is code


def test_household_data_cannot_claim_a_clean_inspection():
    forged = household_ok([entry()])
    forged["inspection"] = {"status": "no_match", "policy_id": "web-fetch-v1"}
    with pytest.raises(ContractError, match="not inspected"):
        TOOL.validate_result(forged)


def test_a_household_envelope_cannot_carry_a_content_inspection_code():
    with pytest.raises(ContractError, match="cannot accompany household_shared"):
        Envelope.failure(
            "memory_recall",
            1,
            ErrorCode.CONTENT_REJECTED,
            Inspection(InspectionStatus.NOT_RUN, None),
            trust=SharedTrust.HOUSEHOLD_SHARED,
        )


def test_a_tool_accepts_only_its_own_trust_value():
    external = Envelope.ok("memory_recall", 1, {"entries": []}, "web-fetch-v1").to_dict()
    with pytest.raises(ContractError, match="household_shared"):
        TOOL.validate_result(external)
    # and a web fetch result cannot claim the household value
    with pytest.raises(ContractError, match="external_untrusted"):
        Envelope.from_dict(household_ok([]), tool="memory_recall", tool_version=1)
    assert Trust.EXTERNAL_UNTRUSTED.value not in {t.value for t in SharedTrust}


def test_the_envelope_still_refuses_an_unknown_trust_value():
    forged = household_ok([])
    forged["trust"] = "trusted"
    with pytest.raises(ContractError, match="not a known value"):
        TOOL.validate_result(forged)


def test_a_forged_tool_identity_is_refused():
    forged = household_ok([])
    forged["tool"] = WEB_FETCH_KEY
    with pytest.raises(ContractError, match="expected memory_recall"):
        TOOL.validate_result(forged)


# ---- size limits -----------------------------------------------------------------


def test_limits_at_the_boundary_pass():
    full = [entry("x" * 1200) for _ in range(RECALL_MAX_ENTRIES)]
    assert sum(len(e["content"]) for e in full) == RECALL_MAX_TOTAL_CONTENT_CHARS
    validate_recall_data({"entries": full})
    validate_recall_data({"entries": [entry("y" * RECALL_MAX_CONTENT_CHARS, tags=["t"] * RECALL_MAX_TAGS)]})


@pytest.mark.parametrize(
    "data, message",
    [
        ({"entries": [entry()] * (RECALL_MAX_ENTRIES + 1)}, "at most 10"),
        ({"entries": [entry("x" * (RECALL_MAX_CONTENT_CHARS + 1))]}, "content must be at most"),
        ({"entries": [entry("x" * 1201)] * RECALL_MAX_ENTRIES}, "total at most"),
        ({"entries": [entry(author="a" * 101)]}, "author"),
        ({"entries": [entry(created_at="2" * 41)]}, "created_at"),
        ({"entries": [entry(tags=["t"] * (RECALL_MAX_TAGS + 1))]}, "tags"),
        ({"entries": [entry(tags=["t" * 51])]}, "tags"),
        ({"entries": [entry(content="")]}, "empty"),
        ({"entries": [{"content": "c", "author": "a", "created_at": "", "tags": [], "id": "1"}]}, "unknown fields: id"),
        ({"entries": [{"content": "c"}]}, "missing"),
        ({"entries": [], "extra": 1}, "unknown fields: extra"),
        ({}, "missing: entries"),
    ],
)
def test_data_limits_fail_closed(data, message):
    with pytest.raises(ContractError, match=message):
        validate_recall_data(data)


# ---- profile -----------------------------------------------------------------------


def test_the_profile_is_the_one_in_the_spec():
    profile = memory_recall_profile("registry.invalid/ai-jobs:1", "http://hearthmem.shared.svc:8765/")
    assert profile.name == "memory-recall-v1"
    assert profile.entrypoint == ("ai-jobs-worker", "memory-recall", "http://hearthmem.shared.svc:8765")
    assert profile.active_deadline_seconds == 15
    assert (profile.resources.cpu_request, profile.resources.cpu_limit) == ("100m", "200m")
    assert (profile.resources.memory_request, profile.resources.memory_limit) == ("64Mi", "128Mi")
    assert profile.result_limit_bytes == 65_536
    assert profile.credential_source == "memory_recall_read_token"
    assert profile.runtime_class is None


@pytest.mark.parametrize(
    "url",
    ["", "ftp://h", "http://", "http://user:pw@h:1", "http://h/stores", "http://h?x=1", "http://h#f", "h:8765"],
)
def test_the_profile_refuses_anything_but_a_plain_service_address(url):
    with pytest.raises(ValueError):
        memory_recall_profile("registry.invalid/ai-jobs:1", url)


# ---- OpenAPI ---------------------------------------------------------------------------

CHECKED_IN = Path(__file__).parents[1] / "openapi.tool-server.json"


def test_the_checked_in_tool_server_document_is_what_the_server_publishes():
    assert json.loads(CHECKED_IN.read_text()) == openapi_document()
    assert CHECKED_IN.read_text() == json.dumps(openapi_document(), indent=2) + "\n"


def test_the_document_offers_the_model_exactly_one_tool():
    document = openapi_document()
    assert list(document["paths"]) == ["/v1/tools/memory-recall"]
    assert list(document["paths"]["/v1/tools/memory-recall"]) == ["post"]
    operation = document["paths"]["/v1/tools/memory-recall"]["post"]
    assert operation["operationId"] == "memory_recall"
    assert operation["security"] == [{"callerKey": []}]
    assert document["components"]["securitySchemes"]["callerKey"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "A per-consumer key.",
    }


def test_the_document_states_the_same_limits_as_the_code():
    schemas = openapi_document()["components"]["schemas"]
    request = schemas["MemoryRecallRequest"]
    assert request["additionalProperties"] is False
    assert request["required"] == ["query"]
    assert request["properties"]["query"]["maxLength"] == 500
    assert (request["properties"]["limit"]["minimum"], request["properties"]["limit"]["maximum"]) == (1, 10)
    assert request["properties"]["limit"]["default"] == 5
    assert schemas["MemoryRecallData"]["properties"]["entries"]["maxItems"] == RECALL_MAX_ENTRIES
    assert schemas["RecallEntry"]["properties"]["content"]["maxLength"] == RECALL_MAX_CONTENT_CHARS
    assert schemas["MemoryRecallEnvelope"]["properties"]["trust"] == {"const": "household_shared"}
    failure_codes = set(schemas["ToolFailure"]["properties"]["code"]["enum"])
    assert failure_codes <= {code.value for code in FailureCode}
    assert ToolFailure(FailureCode.INVALID_REQUEST, "m").code.value in failure_codes


def test_the_request_schema_has_no_runtime_controls():
    text = json.dumps(openapi_document()["components"]["schemas"]["MemoryRecallRequest"]).lower()
    for term in ("image", "command", "pod", "namespace", "environment", "credential", "mount", "token", "store"):
        assert term not in text
