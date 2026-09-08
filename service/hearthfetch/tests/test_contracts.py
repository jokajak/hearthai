import pytest

import hearthfetch.contracts as contracts_module
from hearthfetch.contracts import (
    ContractError,
    FetchResultRequest,
    SearchRequest,
    ToolFailure,
    FailureCode,
)

INFRASTRUCTURE_FIELDS = [
    "image", "command", "namespace", "pod", "credential", "mount",
    "timeout", "model", "tool", "workspace", "env",
]


def test_search_request_minimal_defaults_count():
    assert SearchRequest.from_dict({"query": "when is the next transit"}).count == 5


def test_search_request_trims_and_bounds():
    assert SearchRequest.from_dict({"query": "  spaced  "}).query == "spaced"
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "x", "count": 0})
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "x", "count": 11})
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "x" * 513})
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "   "})


def test_count_rejects_bool_which_is_an_int_subclass():
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "x", "count": True})


@pytest.mark.parametrize("field", INFRASTRUCTURE_FIELDS)
def test_no_request_accepts_an_infrastructure_shaped_field(field):
    """The model chooses what to ask about, never how the work runs."""
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": "x", field: "anything"})
    with pytest.raises(ContractError):
        FetchResultRequest.from_dict({"handle": "h", "question": "q", field: "anything"})


def test_fetch_result_requires_both_fields():
    ok = FetchResultRequest.from_dict({"handle": "hf1.a.b", "question": "what changed"})
    assert ok.handle == "hf1.a.b"
    with pytest.raises(ContractError):
        FetchResultRequest.from_dict({"handle": "hf1.a.b"})
    with pytest.raises(ContractError):
        FetchResultRequest.from_dict({"question": "q"})


def test_non_object_and_non_string_payloads_are_refused():
    with pytest.raises(ContractError):
        SearchRequest.from_dict(["query"])
    with pytest.raises(ContractError):
        SearchRequest.from_dict({"query": 7})


def test_failure_serialises_without_leaking_internals():
    body = ToolFailure(FailureCode.SOURCE_REJECTED, "source rejected").to_dict()
    assert body == {"error": {"code": "source_rejected", "message": "source rejected"}}


def test_no_request_type_accepts_a_url():
    """There is no literal-URL tool, and no request type that could become one.

    A model-composed URL would be an outbound request to a destination chosen
    from attacker-influenced prose. Handles exist so the model manipulates
    references instead, and this asserts nothing quietly reintroduces the
    literal form.
    """
    assert not hasattr(contracts_module, "FetchUrlRequest")
    with pytest.raises(ContractError):
        FetchResultRequest.from_dict(
            {"handle": "h", "question": "q", "url": "https://evil.example/collect"}
        )
