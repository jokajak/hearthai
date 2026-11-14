"""Reviewed worker profiles. The image comes from chart values; the rest is fixed here."""

from __future__ import annotations

from ai_jobs.executor import ResolvedWorkerProfile, Resources


def noop_profile(image: str) -> ResolvedWorkerProfile:
    """``noop-v1``: default runtime, no credential, one tiny envelope."""
    return ResolvedWorkerProfile(
        name="noop-v1",
        image=image,
        entrypoint=("ai-jobs-worker", "noop"),
        active_deadline_seconds=10,
        resources=Resources("50m", "100m", "32Mi", "64Mi"),
        runtime_class=None,
        credential_source=None,
        result_limit_bytes=4_096,
    )
