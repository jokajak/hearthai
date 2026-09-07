"""Execution boundary; implementations may not accept caller-authored profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResolvedWorkerProfile:
    name: str
    active_deadline_seconds: int


class Executor(Protocol):
    def start(self, run_id: str, profile: ResolvedWorkerProfile) -> None: ...
    def cancel(self, run_id: str) -> None: ...
