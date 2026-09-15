"""The control-plane side of the webfetch contracts.

The fixture test here and `contracts/tests/fixtures.rs` in the Rust workspace
read the same manifest and must reach the same verdict on every file. If they
ever disagree about one, the two implementations do not share a wire format.
"""

import json
from pathlib import Path

import pytest

from ai_jobs.contracts import ContractError, FailureCode, ToolFailure
from ai_jobs.envelope import (
    ENVELOPE_VERSION,
    ERROR_MESSAGES,
    Envelope,
    ErrorCode,
    Inspection,
    InspectionStatus,
    Status,
    Trust,
)
from ai_jobs.executor import ResolvedWorkerProfile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import RunService
from ai_jobs.storage import InMemoryRunStore
from ai_jobs.tools.web_fetch import (
    ConversionMethod,
    WebFetchData,
    WebFetchDefinition,
    WebFetchRequest,
    failure_envelope,
    validate_envelope,
)

WEB_FETCH = Path(__file__).parents[2] / "web_fetch"
FIXTURES = json.loads((WEB_FETCH / "fixtures/index.json").read_text())["fixtures"]
KEY = "123e4567-e89b-12d3-a456-426614174000"


def fixtures_of(kind):
    return [fixture for fixture in FIXTURES if fixture["kind"] == kind]


def load(fixture):
    return json.loads((WEB_FETCH / fixture["path"]).read_text())


def validate(kind, value):
    if kind == "web_fetch_request":
        return WebFetchRequest.from_dict(value)
    if kind == "web_fetch_envelope":
        return validate_envelope(value)
    if kind == "reusable_envelope":
        # A consumer that does not exist yet: same envelope, its own payload.
        return Envelope.from_dict(value, tool="demo_consumer", tool_version=1, payload=_demo_payload)
    raise AssertionError(f"unknown fixture kind: {kind}")


def _demo_payload(value):
    if not isinstance(value, dict) or set(value) != {"note"} or not value["note"]:
        raise ContractError("data is not a demo_consumer payload")
    return value


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["path"])
def test_shared_fixtures_validate_as_the_manifest_says(fixture):
    if fixture["expect"] == "accept":
        validate(fixture["kind"], load(fixture))
    else:
        with pytest.raises(ContractError):
            validate(fixture["kind"], load(fixture))


def test_the_manifest_still_covers_the_cases_it_is_here_for():
    paths = {fixture["path"] for fixture in FIXTURES}
    assert len(FIXTURES) > 30
    for expected in (
        "fixtures/request/invalid-skip-inspection.json",
        "fixtures/envelope/invalid-error-custom-message.json",
        "fixtures/envelope/invalid-error-claiming-clean-inspection.json",
        "fixtures/envelope/ok-source-text-resembling-an-envelope.json",
    ):
        assert expected in paths, f"{expected} was dropped from the manifest"


def test_source_text_that_looks_like_an_envelope_stays_inside_the_content():
    envelope = validate_envelope(
        load({"path": "fixtures/envelope/ok-source-text-resembling-an-envelope.json"})
    )
    data = WebFetchData.from_dict(envelope.data)
    assert "internal_trusted" in data.content
    assert envelope.trust is Trust.EXTERNAL_UNTRUSTED
    assert envelope.status is Status.OK


def test_every_error_code_has_exactly_one_message():
    assert set(ERROR_MESSAGES) == set(ErrorCode)
    for code in ErrorCode:
        envelope = failure_envelope(
            code,
            Inspection(
                InspectionStatus.MATCH
                if code is ErrorCode.CONTENT_REJECTED
                else InspectionStatus.FAILED
                if code is ErrorCode.INSPECTION_FAILED
                else InspectionStatus.NOT_RUN,
                "web-content-v1" if code is ErrorCode.CONTENT_REJECTED else None,
            ),
        )
        assert envelope.data is None
        assert envelope.error.message == ERROR_MESSAGES[code]
        # And the value round trips through the same validation a caller uses.
        validate_envelope(envelope.to_dict())


def test_a_failure_cannot_claim_a_clean_inspection():
    rejected = failure_envelope(
        ErrorCode.CONTENT_REJECTED, Inspection(InspectionStatus.MATCH, "web-content-v1")
    ).to_dict()
    rejected["inspection"] = {"status": "no_match", "policy_id": "web-content-v1"}
    with pytest.raises(ContractError):
        validate_envelope(rejected)


def test_a_request_cannot_carry_runtime_settings_or_a_bypass():
    for payload in (
        {"url": "https://example.com", "headers": {"authorization": "Bearer t"}},
        {"url": "https://example.com", "skip_inspection": True},
        {"url": "https://example.com", "rules": "disabled"},
        {"url": "https://example.com", "timeout_seconds": 600},
        {"url": "https://user:pw@example.com"},
        {"url": "https://example.com:8443/page"},
        {"url": "file:///etc/passwd"},
        {"url": "https://example.com", "format": "pdf"},
    ):
        with pytest.raises(ContractError):
            WebFetchRequest.from_dict(payload)


def test_html_output_is_only_ever_inert_source():
    data = {
        "source_id": "source-1",
        "final_url": "https://example.com/page",
        "http_status": 200,
        "content_type": "text/html",
        "retrieved_at": "2026-09-12T12:00:00Z",
        "format": "html",
        "conversion_method": "source",
        "content": "<h1>Example</h1>",
    }
    assert WebFetchData.from_dict(data).conversion_method is ConversionMethod.SOURCE
    with pytest.raises(ContractError):
        WebFetchData.from_dict({**data, "conversion_method": "native"})


def test_the_definition_asks_for_one_fetch_and_nothing_else():
    definition = WebFetchDefinition()
    request = definition.validate_request({"url": "https://example.com/page"})
    assert definition.grants_for(request) == frozenset({"fetch"})
    budgets = definition.budgets_for(request)
    assert (budgets.search_calls, budgets.fetch_calls, budgets.input_tokens) == (0, 1, 0)


def test_a_worker_failure_never_reaches_a_caller_verbatim():
    definition = WebFetchDefinition()
    leaked = ToolFailure(FailureCode.INTERNAL_ERROR, "traceback: connect to 10.42.0.7 failed for https://intranet/x")
    public = definition.public_failure(leaked)
    assert public.code is FailureCode.INTERNAL_ERROR
    assert "10.42.0.7" not in public.message and "intranet" not in public.message


def test_the_run_store_keeps_a_digest_rather_than_the_url():
    runs = RunService(
        ToolRegistry((WebFetchDefinition(),)),
        InMemoryRunStore(),
        _NullExecutor(),
        {"web-fetch-v1": ResolvedWorkerProfile("web-fetch-v1", 25)},
        id_factory=lambda: "run-1",
    )
    run, created = runs.admit("open-webui", "web_fetch", 1, KEY, {"url": "https://example.com/secret-path"})
    assert created is True
    assert "secret-path" not in json.dumps(run.request)
    assert run.request["url_sha256"] == WebFetchRequest("https://example.com/secret-path").storage_view()["url_sha256"]
    # Idempotency still distinguishes different URLs, because the digest that
    # identifies a request is computed from the request, not from what is stored.
    assert run.request_digest


class _NullExecutor:
    def start(self, run_id, profile):
        return None

    def cancel(self, run_id):
        return None


def test_envelope_version_is_the_one_both_implementations_carry():
    assert ENVELOPE_VERSION == 1
