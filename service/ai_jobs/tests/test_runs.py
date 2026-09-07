from datetime import UTC, datetime

import pytest

from ai_jobs.contracts import RunState
from ai_jobs.executor import ResolvedWorkerProfile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import InvalidIdempotencyKey, RunService
from ai_jobs.storage import IdempotencyConflict, InMemoryRunStore, TransitionConflict
from ai_jobs.tools.web_research import WebResearchDefinition

KEY = "123e4567-e89b-12d3-a456-426614174000"


class FakeExecutor:
    def __init__(self):
        self.started = []
        self.cancelled = []

    def start(self, run_id, profile):
        self.started.append((run_id, profile))

    def cancel(self, run_id):
        self.cancelled.append(run_id)


def service():
    executor = FakeExecutor()
    store = InMemoryRunStore()
    identifiers = iter(("run-1", "run-2", "run-3"))
    result = RunService(
        ToolRegistry((WebResearchDefinition(),)),
        store,
        executor,
        {"web-research-v1": ResolvedWorkerProfile("web-research-v1", 90)},
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
        id_factory=lambda: next(identifiers),
    )
    return result, store, executor


def test_admission_resolves_policy_and_starts_fixed_profile():
    runs, _, executor = service()
    run, created = runs.admit("open-webui", "web_research", 1, KEY, {"question": "Current news"})
    assert created is True
    assert run.grants == frozenset({"search", "fetch", "infer"})
    assert run.budgets.elapsed_seconds == 90
    assert executor.started == [("run-1", ResolvedWorkerProfile("web-research-v1", 90))]


def test_matching_retry_reuses_run_and_does_not_start_worker_twice():
    runs, _, executor = service()
    first, _ = runs.admit("open-webui", "web_research", 1, KEY, {"question": "Current news"})
    second, created = runs.admit("open-webui", "web_research", 1, KEY, {"question": "Current news"})
    assert second == first
    assert created is False
    assert len(executor.started) == 1


def test_key_reuse_with_different_request_is_rejected():
    runs, _, _ = service()
    runs.admit("open-webui", "web_research", 1, KEY, {"question": "First"})
    with pytest.raises(IdempotencyConflict):
        runs.admit("open-webui", "web_research", 1, KEY, {"question": "Second"})


def test_idempotency_key_is_scoped_to_caller():
    runs, _, executor = service()
    runs.admit("caller-a", "web_research", 1, KEY, {"question": "Same"})
    # A deterministic ID factory is safe in this isolated test because identity is separately scoped.
    other, created = runs.admit("caller-b", "web_research", 1, KEY, {"question": "Same"})
    assert created is True
    assert other.caller_id == "caller-b"
    assert len(executor.started) == 2


def test_invalid_idempotency_key_fails_before_start():
    runs, _, executor = service()
    with pytest.raises(InvalidIdempotencyKey):
        runs.admit("open-webui", "web_research", 1, "model chose this", {"question": "q"})
    assert executor.started == []


def test_state_transition_is_compare_and_set():
    runs, store, _ = service()
    run, _ = runs.admit("open-webui", "web_research", 1, KEY, {"question": "q"})
    starting = store.transition(run.id, RunState.ACCEPTED, RunState.STARTING)
    assert starting.state is RunState.STARTING
    with pytest.raises(TransitionConflict):
        store.transition(run.id, RunState.ACCEPTED, RunState.RUNNING)


def test_state_machine_rejects_skipping_running_and_terminal_rewrites():
    runs, store, _ = service()
    run, _ = runs.admit("open-webui", "web_research", 1, KEY, {"question": "q"})
    with pytest.raises(TransitionConflict, match="illegal transition"):
        store.transition(run.id, RunState.ACCEPTED, RunState.SUCCEEDED)
    store.transition(run.id, RunState.ACCEPTED, RunState.CANCELLED)
    with pytest.raises(TransitionConflict, match="illegal transition"):
        store.transition(run.id, RunState.CANCELLED, RunState.RUNNING)
