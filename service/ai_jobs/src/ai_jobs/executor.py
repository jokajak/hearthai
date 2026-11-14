"""Execution boundary; implementations may not accept caller-authored profiles.

A profile is reviewed code plus chart values. Nothing in it, and nothing built
from it, comes from a request. The request travels separately, as the bytes in
:class:`ExecutionSpec`, and an executor delivers those bytes without
interpreting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Resources:
    """Kubernetes quantity strings for the worker container."""

    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


@dataclass(frozen=True, slots=True)
class ResolvedWorkerProfile:
    name: str
    image: str
    #: The container command. It takes no arguments from a request.
    entrypoint: tuple[str, ...]
    active_deadline_seconds: int
    resources: Resources
    #: ``None`` runs under the cluster's default runtime; the profile says so.
    runtime_class: str | None = None
    #: A named key of the control plane's own Secret, or ``None``.
    credential_source: str | None = None
    #: The most stdout an executor will read; more than this is a failure.
    result_limit_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not isinstance(self.entrypoint, tuple) or not self.entrypoint:
            raise ValueError("entrypoint must be a non-empty tuple")
        if self.active_deadline_seconds <= 0:
            raise ValueError("active_deadline_seconds must be positive")
        if self.result_limit_bytes <= 0:
            raise ValueError("result_limit_bytes must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    """Built only by trusted code, from a frozen profile and an admitted run."""

    run_id: str
    profile: ResolvedWorkerProfile
    #: Tool-validated request, canonical JSON. Delivered, never interpreted.
    request_json: bytes
    #: Resolved by the profile, never supplied by a caller.
    credential: bytes | None = None

    def __post_init__(self) -> None:
        if (self.profile.credential_source is None) != (self.credential is None):
            raise ValueError("a credential is present exactly when the profile names a source")


@dataclass(frozen=True, slots=True)
class ExecutionRef:
    run_id: str
    #: The executor's handle for the run's objects (for a Sandbox, its name).
    name: str
    #: Carried so ``observe`` can bound its read without the profile.
    result_limit_bytes: int = 65_536


class ObservedState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class FailureReason(StrEnum):
    """Fixed, non-descriptive reasons. No worker text is ever carried."""

    POD_FAILED = "pod_failed"
    SANDBOX_GONE = "sandbox_gone"
    RESULT_TOO_LARGE = "result_too_large"
    MALFORMED_RESULT = "malformed_result"
    NO_RESULT = "no_result"


@dataclass(frozen=True, slots=True)
class Observation:
    state: ObservedState
    #: Set only when ``state`` is ``FAILED``.
    reason: FailureReason | None = None
    #: The parsed last stdout line; set only when ``state`` is ``SUCCEEDED``.
    #: It is untrusted until the tool definition has validated it.
    result: object | None = None


class Executor(Protocol):
    def ensure_started(self, spec: ExecutionSpec) -> ExecutionRef:
        """Idempotent per ``spec.run_id``: the same run never gets two sets of objects."""
        ...

    def observe(self, ref: ExecutionRef) -> Observation: ...

    def request_cancel(self, ref: ExecutionRef) -> None: ...
