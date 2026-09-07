"""Run persistence protocol and deterministic in-memory test adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock
from typing import Protocol

from ai_jobs.contracts import BrokerBudgets, RunState


class IdempotencyConflict(ValueError):
    pass


class TransitionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    caller_id: str
    tool_key: str
    tool_version: int
    idempotency_key: str
    request_digest: str
    request: object
    grants: frozenset[str]
    budgets: BrokerBudgets
    policy_version: str
    state: RunState
    created_at: datetime


class RunStore(Protocol):
    def create_or_get(self, run: Run) -> tuple[Run, bool]: ...
    def get(self, run_id: str) -> Run: ...
    def transition(self, run_id: str, expected: RunState, target: RunState) -> Run: ...


class InMemoryRunStore:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._keys: dict[tuple[str, str, int, str], str] = {}
        self._lock = Lock()

    def create_or_get(self, run: Run) -> tuple[Run, bool]:
        identity = (run.caller_id, run.tool_key, run.tool_version, run.idempotency_key)
        with self._lock:
            existing_id = self._keys.get(identity)
            if existing_id is not None:
                existing = self._runs[existing_id]
                if existing.request_digest != run.request_digest:
                    raise IdempotencyConflict("idempotency key was already used for different content")
                return existing, False
            if run.id in self._runs:
                raise ValueError(f"duplicate run ID: {run.id}")
            self._runs[run.id] = run
            self._keys[identity] = run.id
            return run, True

    def get(self, run_id: str) -> Run:
        with self._lock:
            return self._runs[run_id]

    def transition(self, run_id: str, expected: RunState, target: RunState) -> Run:
        with self._lock:
            current = self._runs[run_id]
            if current.state != expected:
                raise TransitionConflict(f"expected {expected.value}, found {current.state.value}")
            legal = {
                RunState.ACCEPTED: {RunState.STARTING, RunState.CANCELLED, RunState.TIMED_OUT, RunState.FAILED},
                RunState.STARTING: {RunState.RUNNING, RunState.CANCELLED, RunState.TIMED_OUT, RunState.FAILED},
                RunState.RUNNING: {RunState.SUCCEEDED, RunState.CANCELLED, RunState.TIMED_OUT, RunState.FAILED},
            }
            if target not in legal.get(current.state, set()):
                raise TransitionConflict(f"illegal transition: {current.state.value} -> {target.value}")
            updated = replace(current, state=target)
            self._runs[run_id] = updated
            return updated
