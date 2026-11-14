from datetime import UTC, datetime, timedelta

import pytest

from ai_jobs.executor import ExecutionSpec, ResolvedWorkerProfile, Resources
from ai_jobs.profiles import noop_profile


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


def make_profile(**changes):
    values = dict(
        name="test-v1",
        image="registry.invalid/worker:1",
        entrypoint=("worker", "run"),
        active_deadline_seconds=15,
        resources=Resources("100m", "200m", "64Mi", "128Mi"),
        runtime_class=None,
        credential_source=None,
        result_limit_bytes=1024,
    )
    values.update(changes)
    return ResolvedWorkerProfile(**values)


def make_spec(run_id="run-1", request=b"{}", credential=None, **profile_changes):
    if credential is not None:
        profile_changes.setdefault("credential_source", "test_token")
    return ExecutionSpec(run_id, make_profile(**profile_changes), request, credential)


@pytest.fixture
def noop():
    return noop_profile("registry.invalid/ai-jobs:test")
