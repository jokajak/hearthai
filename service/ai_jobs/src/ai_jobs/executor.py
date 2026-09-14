"""Execution boundary; implementations may not accept caller-authored profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResolvedWorkerProfile:
    name: str
    active_deadline_seconds: int


class Executor(Protocol):
    #: `request` is the full validated request, handed straight to the fixed
    #: profile's pod. It is deliberately not routed through the run store: a
    #: tool may have to keep a URL out of durable records while the worker
    #: still needs it.
    def start(self, run_id: str, profile: ResolvedWorkerProfile, request: object) -> None: ...
    def cancel(self, run_id: str) -> None: ...
