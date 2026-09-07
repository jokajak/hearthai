"""Tool-neutral admission and run lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from ai_jobs.contracts import ContractError, RunState
from ai_jobs.executor import Executor, ResolvedWorkerProfile
from ai_jobs.registry import ToolRegistry
from ai_jobs.storage import Run, RunStore


class InvalidIdempotencyKey(ContractError):
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
    ):
        self.registry = registry
        self.store = store
        self.executor = executor
        self.profiles = dict(profiles)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.id_factory = id_factory or (lambda: uuid4().hex)

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
        run = Run(
            id=self.id_factory(),
            caller_id=caller_id,
            tool_key=tool_key,
            tool_version=tool_version,
            idempotency_key=idempotency_key,
            request_digest=digest,
            request=request,
            grants=definition.grants_for(request),
            budgets=definition.budgets_for(request),
            policy_version=f"{tool_key}:v{tool_version}",
            state=RunState.ACCEPTED,
            created_at=self.clock(),
        )
        stored, created = self.store.create_or_get(run)
        if not created:
            return stored, False
        self.executor.start(stored.id, profile)
        return stored, True


def _validate_idempotency_key(value: str) -> None:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidIdempotencyKey("Idempotency-Key must be a UUID") from exc
    if str(parsed) != value.lower():
        raise InvalidIdempotencyKey("Idempotency-Key must use canonical UUID form")
