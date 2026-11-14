"""Tool-neutral admission and run lifecycle."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Callable
from uuid import UUID, uuid4

from ai_jobs.audit import AuditSink, NullAudit
from ai_jobs.contracts import ContractError, FailureCode, RunState, ToolFailure
from ai_jobs.executor import (
    ExecutionRef,
    ExecutionSpec,
    Executor,
    ObservedState,
    ResolvedWorkerProfile,
)
from ai_jobs.registry import ToolDefinition, ToolRegistry
from ai_jobs.storage import Run, RunStore, TransitionConflict


class InvalidIdempotencyKey(ContractError):
    pass


#: One message per code; nothing a worker, the cluster or a validator said is ever repeated.
_FAILURE_MESSAGES = {
    FailureCode.DEADLINE_EXCEEDED: "The request did not complete within its deadline.",
    FailureCode.TEMPORARILY_UNAVAILABLE: "The tool is unavailable.",
    FailureCode.INTERNAL_ERROR: "The tool could not complete this request.",
}

_TERMINAL = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.TIMED_OUT, RunState.CANCELLED})


def fixed_failure(code: FailureCode) -> ToolFailure:
    return ToolFailure(code, _FAILURE_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """A terminal run: the validated result, or a fixed failure. Never both."""

    run: Run
    result: object | None = None
    failure: ToolFailure | None = None


@dataclass
class _Active:
    ref: ExecutionRef
    profile: ResolvedWorkerProfile
    definition: ToolDefinition
    started_at: datetime
    lock: Lock = field(default_factory=Lock)
    outcome: RunOutcome | None = None


class UnknownRun(KeyError):
    pass


class RunService:
    def __init__(
        self,
        registry: ToolRegistry,
        store: RunStore,
        executor: Executor,
        profiles: dict[str, ResolvedWorkerProfile],
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        audit: AuditSink | None = None,
        credential_resolver: Callable[[str], bytes] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 0.5,
    ):
        self.registry = registry
        self.store = store
        self.executor = executor
        self.profiles = dict(profiles)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.audit = audit or NullAudit()
        self.credential_resolver = credential_resolver
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self._active: dict[str, _Active] = {}

    def admit(
        self,
        caller_id: str,
        tool_key: str,
        tool_version: int,
        idempotency_key: str,
        payload: object,
    ) -> tuple[Run, bool]:
        _validate_idempotency_key(idempotency_key)
        definition = self.registry.resolve(tool_key, tool_version)
        try:
            profile = self.profiles[definition.worker_profile]
        except KeyError as exc:
            raise RuntimeError(f"trusted worker profile is not configured: {definition.worker_profile}") from exc
        request = definition.validate_request(payload)
        canonical = json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        # The full request stays in memory for the run; what the store keeps is
        # whatever the tool says is safe to keep. For webfetch that is a URL
        # digest, so a durable record never holds the address that was fetched.
        stored_request = definition.storage_view(request)
        run = Run(
            id=self.id_factory(),
            caller_id=caller_id,
            tool_key=tool_key,
            tool_version=tool_version,
            idempotency_key=idempotency_key,
            request_digest=digest,
            request=stored_request,
            grants=definition.grants_for(request),
            budgets=definition.budgets_for(request),
            policy_version=f"{tool_key}:v{tool_version}",
            state=RunState.ACCEPTED,
            created_at=self.clock(),
        )
        stored, created = self.store.create_or_get(run)
        if not created:
            return stored, False
        self.audit.transition(stored, profile.name)
        try:
            spec = ExecutionSpec(
                run_id=stored.id,
                profile=profile,
                request_json=canonical.encode(),
                credential=self._credential_for(profile),
            )
            ref = self.executor.ensure_started(spec)
        except Exception:
            failed = self.store.transition(stored.id, RunState.ACCEPTED, RunState.FAILED)
            self.audit.transition(failed, profile.name, FailureCode.TEMPORARILY_UNAVAILABLE)
            raise
        self._active[stored.id] = _Active(ref, profile, definition, self.clock())
        starting = self.store.transition(stored.id, RunState.ACCEPTED, RunState.STARTING)
        self.audit.transition(starting, profile.name)
        return starting, True

    def await_result(self, run_id: str) -> RunOutcome:
        """Wait for the run's terminal observation, at most its profile deadline.

        Returns the tool-validated result or a fixed failure. If the deadline passes first the
        run is cancelled and reported as timed out. Calling it again returns the same outcome.
        """
        try:
            active = self._active[run_id]
        except KeyError as exc:
            raise UnknownRun(run_id) from exc
        with active.lock:
            if active.outcome is None:
                active.outcome = self._wait(run_id, active)
            return active.outcome

    def cancel(self, run_id: str) -> Run:
        """Delete the run's objects and record it cancelled. A finished run is left as it is."""
        try:
            active = self._active[run_id]
        except KeyError as exc:
            raise UnknownRun(run_id) from exc
        self.executor.request_cancel(active.ref)
        current = self.store.get(run_id)
        if current.state in _TERMINAL:
            return current
        try:
            cancelled = self.store.transition(run_id, current.state, RunState.CANCELLED)
        except TransitionConflict:
            return self.store.get(run_id)
        self.audit.transition(cancelled, active.profile.name)
        return cancelled

    def _wait(self, run_id: str, active: _Active) -> RunOutcome:
        profile = active.profile
        deadline = active.started_at + timedelta(seconds=profile.active_deadline_seconds)
        while True:
            run = self.store.get(run_id)
            if run.state is RunState.CANCELLED:
                return RunOutcome(run, failure=active.definition.public_failure(fixed_failure(FailureCode.TEMPORARILY_UNAVAILABLE)))
            try:
                observation = self.executor.observe(active.ref)
            except Exception:
                # A cluster hiccup is not a verdict on the run; the deadline still bounds the wait.
                observation = None
            if observation is not None:
                if observation.state is ObservedState.RUNNING:
                    self._advance(run_id, RunState.RUNNING, profile.name)
                elif observation.state is ObservedState.SUCCEEDED:
                    return self._finish_succeeded(run_id, active, observation.result)
                elif observation.state is ObservedState.TIMED_OUT:
                    return self._finish_failed(run_id, active, RunState.TIMED_OUT, FailureCode.DEADLINE_EXCEEDED)
                elif observation.state is ObservedState.FAILED:
                    return self._finish_failed(run_id, active, RunState.FAILED, FailureCode.INTERNAL_ERROR)
            if self.clock() >= deadline:
                self.executor.request_cancel(active.ref)
                return self._finish_failed(run_id, active, RunState.TIMED_OUT, FailureCode.DEADLINE_EXCEEDED)
            self.sleep(self.poll_seconds)

    def _finish_succeeded(self, run_id: str, active: _Active, raw: object) -> RunOutcome:
        try:
            result = active.definition.validate_result(raw)
        except (ValueError, TypeError):
            return self._finish_failed(run_id, active, RunState.FAILED, FailureCode.INTERNAL_ERROR)
        self._advance(run_id, RunState.RUNNING, active.profile.name)
        run = self._advance(run_id, RunState.SUCCEEDED, active.profile.name)
        return RunOutcome(run, result=result)

    def _finish_failed(self, run_id: str, active: _Active, state: RunState, code: FailureCode) -> RunOutcome:
        failure = active.definition.public_failure(fixed_failure(code))
        run = self._advance(run_id, state, active.profile.name, failure.code)
        return RunOutcome(run, failure=failure)

    def _advance(self, run_id: str, target: RunState, profile: str, code: FailureCode | None = None) -> Run:
        """Move forward one step if not already there; the store's compare-and-set decides."""
        current = self.store.get(run_id)
        if current.state is target or current.state in _TERMINAL:
            return current
        if target is RunState.RUNNING and current.state is not RunState.STARTING:
            return current
        try:
            updated = self.store.transition(run_id, current.state, target)
        except TransitionConflict:
            return self.store.get(run_id)
        self.audit.transition(updated, profile, code)
        return updated

    def _credential_for(self, profile: ResolvedWorkerProfile) -> bytes | None:
        if profile.credential_source is None:
            return None
        if self.credential_resolver is None:
            raise RuntimeError(f"profile {profile.name} needs a credential and no resolver is configured")
        return self.credential_resolver(profile.credential_source)


def _validate_idempotency_key(value: str) -> None:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidIdempotencyKey("Idempotency-Key must be a UUID") from exc
    if str(parsed) != value.lower():
        raise InvalidIdempotencyKey("Idempotency-Key must use canonical UUID form")
