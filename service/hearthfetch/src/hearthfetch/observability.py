"""Metrics and audit events, with redaction enforced rather than intended.

Everything this service handles is either attacker-authored or the household's
private business: page content, distillations, search queries, the questions
people ask. None of it belongs in a metric label or a log field, and "we were
careful" is not a control. So label values are validated against a shape that
free text cannot satisfy, and audit events accept a closed set of fields.

Cardinality follows from the same rule: a label that cannot hold a URL also
cannot hold ten thousand distinct values.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

# A label value is a short lowercase token. A URL, a query, a question, or a
# page cannot match this — which is the point. It is a filter on shape, not a
# reminder to be careful.
SAFE_LABEL_VALUE = re.compile(r"^[a-z0-9_]{1,40}$")
SAFE_LABEL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")

# Audit events carry these and nothing else. Adding a field is a deliberate act
# with a reviewer, not something a caller can do in passing.
AUDIT_FIELDS = frozenset({"event", "stage", "rule", "reason", "outcome", "verdict", "tool"})


class UnsafeLabel(ValueError):
    """A label or field would have carried content out of the service."""


def _check_labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    out = []
    for name, value in sorted(labels.items()):
        if not SAFE_LABEL_NAME.match(name):
            raise UnsafeLabel(f"label name {name!r} is not a safe identifier")
        if not isinstance(value, str) or not SAFE_LABEL_VALUE.match(value):
            # Deliberately does not echo the value: an exception message ends up
            # in a log, which is the thing being protected.
            raise UnsafeLabel(f"label {name!r} has a value that is not a safe token")
        out.append((name, value))
    return tuple(out)


@dataclass
class Metrics:
    """A tiny in-process registry rendering Prometheus text format."""

    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=dict)
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(
        default_factory=dict
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def increment(self, name: str, **labels: str) -> None:
        key = (name, _check_labels(labels))
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + 1

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = (name, _check_labels(labels))
        with self._lock:
            self.histograms.setdefault(key, []).append(seconds)

    def value(self, name: str, **labels: str) -> int:
        return self.counters.get((name, _check_labels(labels)), 0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), count in sorted(self.counters.items()):
                lines.append(f"{name}{_render_labels(labels)} {count}")
            for (name, labels), values in sorted(self.histograms.items()):
                rendered = _render_labels(labels)
                lines.append(f"{name}_count{rendered} {len(values)}")
                lines.append(f"{name}_sum{rendered} {sum(values):.6f}")
        return "\n".join(lines) + "\n"


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{name}="{value}"' for name, value in labels)
    return "{" + inner + "}"


def audit(**fields: str) -> dict[str, str]:
    """Build a structured audit record, or refuse.

    Returns the record rather than writing it, so the caller chooses the sink
    and the test can assert on the content without capturing a logger.
    """
    unknown = fields.keys() - AUDIT_FIELDS
    if unknown:
        raise UnsafeLabel(f"audit fields not permitted: {sorted(unknown)}")
    for name, value in fields.items():
        if not isinstance(value, str) or not SAFE_LABEL_VALUE.match(value):
            raise UnsafeLabel(f"audit field {name!r} has a value that is not a safe token")
    return dict(fields)
