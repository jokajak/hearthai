"""Run audit: one OpenTelemetry log record per transition, one span per run.

Every record carries exactly the attributes in :data:`ALLOWED_ATTRIBUTES` and the
event name ``ai_jobs.run.transition``. It never carries a request body, a result
body or a credential: the values come from a :class:`Run`, whose request field is
the tool's storage view and is not read here at all.

The same record is written as one JSON line on stdout, so a dropped export is
not a lost record. Export is configured only through the standard ``OTEL_*``
environment; with no endpoint set, nothing is exported and nothing fails.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Callable, Protocol, TextIO

from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ai_jobs.contracts import FailureCode, RunState
from ai_jobs.storage import Run

EVENT_NAME = "ai_jobs.run.transition"

ALLOWED_ATTRIBUTES = frozenset(
    {
        "ai_jobs.run.id",
        "ai_jobs.caller.id",
        "ai_jobs.tool.key",
        "ai_jobs.tool.version",
        "ai_jobs.request.digest",
        "ai_jobs.profile",
        "ai_jobs.run.state",
        "ai_jobs.failure.code",
    }
)

_TERMINAL = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.TIMED_OUT, RunState.CANCELLED})


class AuditSink(Protocol):
    def transition(self, run: Run, profile: str, failure_code: FailureCode | None = None) -> None: ...


class NullAudit:
    def transition(self, run: Run, profile: str, failure_code: FailureCode | None = None) -> None:
        return None


def attributes_for(run: Run, profile: str, failure_code: FailureCode | None) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "ai_jobs.run.id": run.id,
        "ai_jobs.caller.id": run.caller_id,
        "ai_jobs.tool.key": run.tool_key,
        "ai_jobs.tool.version": run.tool_version,
        "ai_jobs.request.digest": run.request_digest,
        "ai_jobs.profile": profile,
        "ai_jobs.run.state": run.state.value,
    }
    if failure_code is not None:
        attributes["ai_jobs.failure.code"] = failure_code.value
    return attributes


class Audit:
    def __init__(
        self,
        *,
        logger_provider: LoggerProvider | None = None,
        tracer_provider: TracerProvider | None = None,
        stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._logger = (logger_provider or LoggerProvider()).get_logger("ai_jobs.audit")
        self._tracer = (tracer_provider or TracerProvider()).get_tracer("ai_jobs.audit")
        self._logger_provider = logger_provider
        self._tracer_provider = tracer_provider
        self._stream = stream
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spans: dict[str, Any] = {}
        self._lock = Lock()

    @classmethod
    def from_env(cls, *, stream: TextIO | None = None) -> "Audit":
        """Providers configured from ``OTEL_*`` only. No endpoint means no exporter."""
        resource = Resource.create()
        logger_provider = LoggerProvider(resource=resource)
        if _exporter_configured("LOGS", "OTEL_LOGS_EXPORTER"):
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

            logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        tracer_provider = TracerProvider(resource=resource)
        if _exporter_configured("TRACES", "OTEL_TRACES_EXPORTER"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        return cls(logger_provider=logger_provider, tracer_provider=tracer_provider, stream=stream)

    def transition(self, run: Run, profile: str, failure_code: FailureCode | None = None) -> None:
        attributes = attributes_for(run, profile, failure_code)
        now = self._clock()
        with self._lock:
            span = self._spans.get(run.id)
            if span is None:
                span = self._tracer.start_span("ai_jobs.run", attributes=attributes)
                self._spans[run.id] = span
            else:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            context = trace.set_span_in_context(span)
            span_context = span.get_span_context()
            self._logger.emit(
                timestamp=int(now.timestamp() * 1_000_000_000),
                severity_number=SeverityNumber.WARN if failure_code else SeverityNumber.INFO,
                severity_text="WARN" if failure_code else "INFO",
                body=EVENT_NAME,
                attributes=attributes,
                event_name=EVENT_NAME,
                context=context,
            )
            if run.state in _TERMINAL:
                span.end()
                del self._spans[run.id]
        line = {
            "time": now.isoformat(),
            "event_name": EVENT_NAME,
            "body": EVENT_NAME,
            "severity": "WARN" if failure_code else "INFO",
            "attributes": attributes,
            "trace_id": format(span_context.trace_id, "032x"),
            "span_id": format(span_context.span_id, "016x"),
        }
        stream = self._stream if self._stream is not None else sys.stdout
        stream.write(json.dumps(line, sort_keys=True) + "\n")
        stream.flush()

    def shutdown(self) -> None:
        if self._logger_provider is not None:
            self._logger_provider.shutdown()
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()


def _exporter_configured(signal: str, selector: str) -> bool:
    if os.environ.get(selector, "").strip().lower() == "none":
        return False
    return bool(
        os.environ.get(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    )
