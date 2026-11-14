"""Reviewed worker profiles. The image comes from chart values; the rest is fixed here."""

from __future__ import annotations

from urllib.parse import urlsplit

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


def memory_recall_profile(image: str, hearthmem_url: str) -> ResolvedWorkerProfile:
    """``memory-recall-v1``: one read of the household store with a read-only token.

    The hearthmem URL is part of the container command, so it is fixed by this profile and the
    chart value behind it. The worker reads the token from the run's Secret and nothing from the
    request can change where it connects.
    """
    parsed = urlsplit(hearthmem_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("hearthmem_url must be a plain http(s) service address")
    return ResolvedWorkerProfile(
        name="memory-recall-v1",
        image=image,
        entrypoint=("ai-jobs-worker", "memory-recall", hearthmem_url.rstrip("/")),
        active_deadline_seconds=15,
        resources=Resources("100m", "200m", "64Mi", "128Mi"),
        runtime_class=None,  # default runtime; gVisor is not required for this profile (E9)
        credential_source="memory_recall_read_token",
        result_limit_bytes=65_536,
    )
