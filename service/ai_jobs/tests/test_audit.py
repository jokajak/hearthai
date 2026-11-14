"""Audit: one OTel log record per transition, attributes exactly the allowlist, stdout mirrors it."""

import io
import json

import pytest
from conftest import Clock
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_jobs.audit import ALLOWED_ATTRIBUTES, EVENT_NAME, Audit, _exporter_configured
from ai_jobs.cluster import FakeClusterApi
from ai_jobs.contracts import FailureCode, RunState
from ai_jobs.profiles import noop_profile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import RunService
from ai_jobs.sandbox import SandboxExecutor, object_name
from ai_jobs.storage import InMemoryRunStore
from ai_jobs.tools.web_research import WebResearchDefinition

KEY = "123e4567-e89b-12d3-a456-426614174000"
QUESTION = "MARKER-question-for-audit-91c4"


class Rig:
    def __init__(self):
        self.logs = InMemoryLogRecordExporter()
        self.spans = InMemorySpanExporter()
        logger_provider = LoggerProvider()
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(self.logs))
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.stdout = io.StringIO()
        self.clock = Clock()
        self.audit = Audit(
            logger_provider=logger_provider, tracer_provider=tracer_provider, stream=self.stdout, clock=self.clock
        )

    def records(self):
        return [item.log_record for item in self.logs.get_finished_logs()]

    def lines(self):
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]


@pytest.fixture
def rig():
    return Rig()


def service(rig, credential=False):
    """A web_research run (request carries a marker) on the fake cluster, with audit attached."""
    from dataclasses import replace

    api = FakeClusterApi(rig.clock)
    base = noop_profile("registry.invalid/ai-jobs:test")
    profile = replace(
        base,
        name="web-research-v1",
        credential_source="tok" if credential else None,
        active_deadline_seconds=5,
    )
    runs = RunService(
        ToolRegistry((WebResearchDefinition(),)),
        InMemoryRunStore(),
        SandboxExecutor(api, clock=rig.clock),
        {"web-research-v1": profile},
        clock=rig.clock,
        id_factory=lambda: "run-1",
        audit=rig.audit,
        credential_resolver=lambda source: b"MARKER-credential-5b7e",
        sleep=lambda s: rig.clock.advance(s),
    )
    return runs, api


def test_one_record_per_transition_with_exactly_the_allowlist(rig):
    runs, api = service(rig)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    api.finish_failed(object_name("run-1"))
    runs.await_result("run-1")
    records = rig.records()
    assert [r.attributes["ai_jobs.run.state"] for r in records] == ["accepted", "starting", "failed"]
    for record in records:
        assert set(record.attributes) <= ALLOWED_ATTRIBUTES
        assert record.event_name == EVENT_NAME
        assert record.attributes["ai_jobs.run.id"] == "run-1"
        assert record.attributes["ai_jobs.caller.id"] == "open-webui"
        assert record.attributes["ai_jobs.tool.key"] == "web_research"
        assert record.attributes["ai_jobs.tool.version"] == 1
        assert record.attributes["ai_jobs.profile"] == "web-research-v1"
        assert len(record.attributes["ai_jobs.request.digest"]) == 64
    assert "ai_jobs.failure.code" not in records[0].attributes
    assert records[-1].attributes["ai_jobs.failure.code"] == FailureCode.INTERNAL_ERROR.value


def test_the_full_attribute_set_is_the_allowlist_and_nothing_wider(rig):
    runs, api = service(rig)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    api.finish_deadline(object_name("run-1"))
    runs.await_result("run-1")
    seen = set().union(*(set(r.attributes) for r in rig.records()))
    assert seen == ALLOWED_ATTRIBUTES


def test_no_request_body_result_or_credential_in_any_record_span_or_line(rig):
    runs, api = service(rig, credential=True)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    api.finish_succeeded(object_name("run-1"), b'{"result": "MARKER-result-body-0d2f"}\n')
    runs.await_result("run-1")
    everything = json.dumps(
        {
            "records": [
                {"attributes": dict(r.attributes), "body": r.body, "event": r.event_name} for r in rig.records()
            ],
            "spans": [dict(s.attributes) | {"name": s.name} for s in rig.spans.get_finished_spans()],
            "stdout": rig.stdout.getvalue(),
        },
        default=str,
    )
    for marker in ("MARKER", QUESTION, "value-of", "tok"):
        assert marker not in everything
    assert "question" not in everything


def test_stdout_line_mirrors_the_record(rig):
    runs, api = service(rig)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    api.finish_deadline(object_name("run-1"))
    runs.await_result("run-1")
    records, lines = rig.records(), rig.lines()
    assert len(records) == len(lines) == 3
    for record, line in zip(records, lines, strict=True):
        assert line["attributes"] == dict(record.attributes)
        assert line["event_name"] == record.event_name == EVENT_NAME
        assert line["body"] == record.body
        assert line["trace_id"] == format(record.trace_id, "032x")
        assert line["span_id"] == format(record.span_id, "016x")
    assert all(line.count("\n") == 0 for line in rig.stdout.getvalue().splitlines())


def test_one_span_per_run_ending_at_the_terminal_state_with_the_same_attributes(rig):
    runs, api = service(rig)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    assert rig.spans.get_finished_spans() == ()
    api.finish_deadline(object_name("run-1"))
    runs.await_result("run-1")
    (span,) = rig.spans.get_finished_spans()
    assert set(span.attributes) <= ALLOWED_ATTRIBUTES
    assert span.attributes["ai_jobs.run.state"] == "timed_out"
    assert span.attributes["ai_jobs.failure.code"] == "deadline_exceeded"
    assert {r.trace_id for r in rig.records()} == {span.context.trace_id}


def test_failures_log_at_warn_and_successes_at_info(rig):
    runs, api = service(rig)
    runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    api.finish_failed(object_name("run-1"))
    runs.await_result("run-1")
    assert [r.severity_text for r in rig.records()] == ["INFO", "INFO", "WARN"]


def test_attribute_values_come_from_the_run_not_from_its_stored_request(rig):
    runs, _ = service(rig)
    run, _ = runs.admit("open-webui", "web_research", 1, KEY, {"question": QUESTION})
    assert QUESTION in json.dumps(run.request)  # the store view holds it ...
    assert QUESTION not in json.dumps([dict(r.attributes) for r in rig.records()])  # ... audit does not


class TestFromEnv:
    def clear(self, monkeypatch):
        for name in list(__import__("os").environ):
            if name.startswith("OTEL_"):
                monkeypatch.delenv(name)

    def test_no_endpoint_means_no_exporter_and_no_error(self, monkeypatch):
        self.clear(monkeypatch)
        stream = io.StringIO()
        audit = Audit.from_env(stream=stream)
        runs_clock = Clock()
        runs = RunService(
            ToolRegistry((WebResearchDefinition(),)),
            InMemoryRunStore(),
            _Instant(),
            {"web-research-v1": _profile()},
            clock=runs_clock,
            id_factory=lambda: "run-1",
            audit=audit,
        )
        runs.admit("open-webui", "web_research", 1, KEY, {"question": "q"})
        assert len(stream.getvalue().splitlines()) == 2
        audit.shutdown()

    def test_only_the_standard_environment_selects_exporters(self, monkeypatch):
        self.clear(monkeypatch)
        assert (_exporter_configured("LOGS", "OTEL_LOGS_EXPORTER"), _exporter_configured("TRACES", "OTEL_TRACES_EXPORTER")) == (False, False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://collector.invalid:4318/v1/logs")
        assert _exporter_configured("LOGS", "OTEL_LOGS_EXPORTER") is True
        assert _exporter_configured("TRACES", "OTEL_TRACES_EXPORTER") is False
        monkeypatch.setenv("OTEL_LOGS_EXPORTER", "none")
        assert _exporter_configured("LOGS", "OTEL_LOGS_EXPORTER") is False

    def test_a_configured_endpoint_builds_an_exporter_without_connecting(self, monkeypatch):
        self.clear(monkeypatch)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://collector.invalid:4318/v1/logs")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector.invalid:4318/v1/traces")
        audit = Audit.from_env(stream=io.StringIO())
        assert audit._logger_provider is not None and audit._tracer_provider is not None

    def test_service_name_comes_from_otel_service_name(self, monkeypatch):
        self.clear(monkeypatch)
        monkeypatch.setenv("OTEL_SERVICE_NAME", "ai-jobs")
        audit = Audit.from_env(stream=io.StringIO())
        assert audit._logger_provider.resource.attributes["service.name"] == "ai-jobs"


def _profile():
    return noop_profile("registry.invalid/x:1").__class__(
        name="web-research-v1",
        image="registry.invalid/x:1",
        entrypoint=("w",),
        active_deadline_seconds=5,
        resources=noop_profile("x").resources,
    )


class _Instant:
    def ensure_started(self, spec):
        from ai_jobs.executor import ExecutionRef

        return ExecutionRef(spec.run_id, spec.run_id)

    def request_cancel(self, ref):
        pass
