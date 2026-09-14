"""The control plane's half of web_fetch:v1, checked against the shared corpus.

``service/web_fetch/fixtures/manifest.json`` is the single list of cases, and
the Rust contracts crate replays the same file. If the two implementations ever
disagree about a document, one of these runs fails rather than the difference
going unnoticed until a worker result is admitted that should not have been.
"""

import json
from pathlib import Path

import pytest

from ai_jobs.contracts import ContractError, FailureCode, ToolFailure
from ai_jobs.envelope import (
    EnvelopeErrorCode,
    EnvelopeStatus,
    Inspection,
    InspectionStatus,
    ToolResultEnvelope,
    Trust,
)
from ai_jobs.registry import ToolRegistry, UnknownTool
from ai_jobs.tools.web_fetch import (
    CONVERSION_METHODS,
    FORMATS,
    WebFetchData,
    WebFetchDefinition,
    WebFetchRequest,
    admitted,
    validate_envelope,
    withheld,
)

FIXTURES = Path(__file__).parents[2] / "web_fetch" / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def _cases(section):
    return [
        pytest.param(case["file"], case["valid"], case["reason"], id=Path(case["file"]).stem)
        for case in MANIFEST[section]
    ]


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("file,valid,reason", _cases("requests"))
def test_request_fixtures_match_the_manifest(file, valid, reason):
    if valid:
        assert WebFetchRequest.from_dict(_load(file)), reason
    else:
        with pytest.raises(ContractError):
            WebFetchRequest.from_dict(_load(file))


@pytest.mark.parametrize("file,valid,reason", _cases("envelopes"))
def test_envelope_fixtures_match_the_manifest(file, valid, reason):
    if valid:
        assert validate_envelope(_load(file)), reason
    else:
        with pytest.raises(ContractError):
            validate_envelope(_load(file))


@pytest.mark.parametrize("file,valid,reason", _cases("envelopes"))
def test_valid_envelopes_round_trip(file, valid, reason):
    if not valid:
        return
    original = _load(file)
    assert validate_envelope(original).to_dict() == original


def test_the_corpus_did_not_quietly_shrink():
    assert len(MANIFEST["requests"]) >= 12
    assert len(MANIFEST["envelopes"]) >= 20


def test_the_default_format_is_markdown_and_is_recorded_explicitly():
    request = WebFetchRequest.from_dict({"url": "https://example.com/page"})
    assert request.format == "markdown"
    assert request.to_dict() == {"url": "https://example.com/page", "format": "markdown"}


def test_content_that_imitates_an_envelope_stays_inside_the_payload():
    envelope = validate_envelope(_load("envelopes/ok-content-imitating-envelope.json"))
    assert envelope.status is EnvelopeStatus.OK
    assert envelope.trust is Trust.EXTERNAL_UNTRUSTED
    # The imitation is a string, and the real envelope's trust marker is
    # untouched by what the string claims.
    assert '"trust":"trusted_internal"' in envelope.data.content
    assert envelope.data.format == "markdown"


def test_a_rejection_cannot_be_built_with_a_payload():
    data = WebFetchData.from_dict(_load("envelopes/ok-markdown.json")["data"])
    with pytest.raises(ContractError):
        ToolResultEnvelope(
            tool="web_fetch",
            tool_version=1,
            status=EnvelopeStatus.REJECTED,
            trust=Trust.EXTERNAL_UNTRUSTED,
            inspection=Inspection(InspectionStatus.MATCH, "web-content-v1"),
            data=data,
            error=None,
        )._check()


def test_the_gate_constructors_produce_the_documented_envelopes():
    data = WebFetchData.from_dict(_load("envelopes/ok-markdown.json")["data"])
    assert admitted(data).to_dict() == _load("envelopes/ok-markdown.json")

    rejection = withheld(
        EnvelopeErrorCode.CONTENT_REJECTED, Inspection(InspectionStatus.MATCH, "web-content-v1")
    )
    assert rejection.to_dict() == _load("envelopes/rejected-content.json")

    unsafe = withheld(EnvelopeErrorCode.UNSAFE_SOURCE, Inspection(InspectionStatus.NOT_RUN, None))
    assert unsafe.to_dict() == _load("envelopes/error-unsafe-source.json")


def test_every_failure_code_has_exactly_one_message():
    messages = {code: withheld(code, _inspection_for(code)).error.message for code in EnvelopeErrorCode}
    assert len(set(messages.values())) == len(EnvelopeErrorCode)
    for message in messages.values():
        assert not any(marker in message for marker in ("http", "://", "<", "rule "))


def _inspection_for(code):
    if code is EnvelopeErrorCode.CONTENT_REJECTED:
        return Inspection(InspectionStatus.MATCH, "web-content-v1")
    return Inspection(InspectionStatus.FAILED, "web-content-v1")


def test_a_worker_failure_never_carries_its_own_message_to_the_caller():
    definition = WebFetchDefinition()
    leaked = ToolFailure(
        code=FailureCode.UNSAFE_SOURCE,
        message="connect to 10.42.0.7 failed while fetching https://internal.example.com/secret",
    )
    public = definition.public_failure(leaked)
    assert public.code is FailureCode.UNSAFE_SOURCE
    assert "internal.example.com" not in public.message
    assert "10.42.0.7" not in public.message

    # A code with no public meaning collapses rather than passing through.
    internal = ToolFailure(code=FailureCode.PROVIDER_UNAVAILABLE, message="yara scanner aborted: signal 11")
    assert definition.public_failure(internal).code is FailureCode.INTERNAL_ERROR
    assert "yara" not in definition.public_failure(internal).message


def test_the_definition_registers_alongside_the_existing_tools():
    from ai_jobs.tools.web_research import WebResearchDefinition

    definition = WebFetchDefinition()
    registry = ToolRegistry((WebResearchDefinition(), definition))
    assert registry.resolve("web_fetch", 1) is definition
    with pytest.raises(UnknownTool):
        registry.resolve("web_fetch", 2)


def test_the_request_grants_one_fetch_and_nothing_else():
    definition = WebFetchDefinition()
    request = definition.validate_request({"url": "https://example.com/page"})
    assert definition.grants_for(request) == frozenset({"fetch"})
    budgets = definition.budgets_for(request)
    assert budgets.fetch_calls == 1
    assert budgets.search_calls == 0
    assert budgets.input_tokens == 0 and budgets.output_tokens == 0


def test_the_stored_request_digest_does_not_depend_on_a_url_being_persisted():
    # Admission canonicalises the validated request, which does contain the URL.
    # What must not happen is the run store keeping it: the digest is what the
    # idempotency contract needs, and it is one-way.
    import hashlib

    request = WebFetchRequest.from_dict({"url": "https://example.com/private-path"})
    canonical = json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    assert "private-path" not in digest
    assert len(digest) == 64


def test_the_published_schemas_and_openapi_agree_with_the_code():
    """Three representations of one contract: the language-neutral schemas the
    workers validate against, the OpenAPI document the adapter is generated
    from, and these types. Drift between them is a difference nobody notices
    until a worker returns something the control plane admits by accident."""
    schemas = FIXTURES.parent / "schemas"
    request_schema = json.loads((schemas / "web-fetch-request.v1.schema.json").read_text())
    data_schema = json.loads((schemas / "web-fetch-data.v1.schema.json").read_text())
    error_schema = json.loads((schemas / "tool-error.v1.schema.json").read_text())
    openapi = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())
    components = openapi["components"]["schemas"]

    assert tuple(request_schema["properties"]["format"]["enum"]) == FORMATS
    assert tuple(components["WebFetchRequest"]["properties"]["format"]["enum"]) == FORMATS
    assert request_schema["additionalProperties"] is False

    assert tuple(data_schema["properties"]["conversion_method"]["enum"]) == CONVERSION_METHODS
    assert tuple(components["WebFetchData"]["properties"]["conversion_method"]["enum"]) == CONVERSION_METHODS
    assert set(data_schema["required"]) == set(components["WebFetchData"]["required"])

    codes = tuple(code.value for code in EnvelopeErrorCode)
    assert tuple(error_schema["properties"]["code"]["enum"]) == codes
    assert tuple(components["EnvelopeError"]["properties"]["code"]["enum"]) == codes

    envelope_schema = json.loads((schemas / "tool-result-envelope.v1.schema.json").read_text())
    assert set(envelope_schema["required"]) == set(components["WebFetchEnvelope"]["required"])
    assert tuple(envelope_schema["properties"]["trust"]["enum"]) == tuple(t.value for t in Trust)
    assert tuple(envelope_schema["properties"]["inspection"]["properties"]["status"]["enum"]) == tuple(
        status.value for status in InspectionStatus
    )


def test_the_run_store_never_receives_the_url():
    """The digest gives idempotency; a durable list of every URL the model was
    pointed at is a different thing, and this tool does not accumulate one."""
    from datetime import UTC, datetime

    from ai_jobs.executor import ResolvedWorkerProfile
    from ai_jobs.runs import RunService
    from ai_jobs.storage import InMemoryRunStore

    class RecordingExecutor:
        def __init__(self):
            self.requests = []

        def start(self, run_id, profile, request):
            self.requests.append(request)

        def cancel(self, run_id):
            pass

    executor = RecordingExecutor()
    store = InMemoryRunStore()
    service = RunService(
        ToolRegistry((WebFetchDefinition(),)),
        store,
        executor,
        {"web-fetch-v1": ResolvedWorkerProfile("web-fetch-v1", 45)},
        clock=lambda: datetime(2026, 9, 12, tzinfo=UTC),
        id_factory=lambda: "run-1",
    )

    url = "https://example.com/a-path-nobody-should-keep"
    run, created = service.admit(
        "open-webui", "web_fetch", 1, "123e4567-e89b-12d3-a456-426614174000", {"url": url}
    )
    assert created is True

    stored = json.dumps(store.get("run-1").durable_request)
    assert "a-path-nobody-should-keep" not in stored
    assert "example.com" not in stored
    assert stored == '{"format": "markdown"}'

    # The worker still gets what it needs to do the fetch.
    assert executor.requests[0].url == url
    assert run.grants == frozenset({"fetch"})

    # And idempotency still works off the full request.
    _, again = service.admit(
        "open-webui", "web_fetch", 1, "123e4567-e89b-12d3-a456-426614174000", {"url": url}
    )
    assert again is False
