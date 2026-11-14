"""SandboxExecutor against the fake cluster."""

import json
from datetime import timedelta

import pytest
from conftest import make_spec

from ai_jobs.cluster import ClusterConflict, FakeClusterApi
from ai_jobs.executor import FailureReason, ObservedState
from ai_jobs.sandbox import SandboxExecutor, object_name


@pytest.fixture
def api(clock):
    return FakeClusterApi(clock)


@pytest.fixture
def executor(api, clock):
    return SandboxExecutor(api, clock=clock)


def test_start_creates_one_sandbox_and_an_owned_secret(api, executor):
    ref = executor.ensure_started(make_spec(request=b'{"x":1}'))
    assert ref.name == object_name("run-1")
    assert set(api.sandboxes) == {ref.name}
    assert set(api.secrets) == {ref.name}
    owner = api.secrets[ref.name]["metadata"]["ownerReferences"][0]
    assert owner["uid"] == api.sandboxes[ref.name]["metadata"]["uid"]
    assert ref.result_limit_bytes == 1024


def test_start_is_idempotent_per_run(api, executor):
    first = executor.ensure_started(make_spec())
    second = executor.ensure_started(make_spec())
    assert first == second
    assert (api.created_sandboxes, api.created_secrets) == (1, 1)


def test_start_after_a_lost_secret_repairs_it_without_a_second_sandbox(api, executor):
    ref = executor.ensure_started(make_spec())
    del api.secrets[ref.name]
    executor.ensure_started(make_spec())
    assert api.created_sandboxes == 1
    assert set(api.secrets) == {ref.name}


def test_start_survives_a_create_race(api, executor, monkeypatch):
    real = api.create_sandbox

    def raced(manifest):
        real(manifest)
        raise ClusterConflict("created by another caller")

    monkeypatch.setattr(api, "create_sandbox", raced)
    ref = executor.ensure_started(make_spec())
    assert set(api.sandboxes) == {ref.name} and set(api.secrets) == {ref.name}


def test_a_secret_failure_removes_the_sandbox(api, executor, monkeypatch):
    def boom(manifest):
        raise RuntimeError("quota")

    monkeypatch.setattr(api, "create_secret", boom)
    with pytest.raises(RuntimeError):
        executor.ensure_started(make_spec())
    assert api.sandboxes == {}


def test_a_sandbox_that_never_starts_stays_pending(api, executor):
    ref = executor.ensure_started(make_spec())
    assert executor.observe(ref).state is ObservedState.PENDING


def test_running(api, executor):
    ref = executor.ensure_started(make_spec())
    api.set_running(ref.name)
    assert executor.observe(ref).state is ObservedState.RUNNING


ENVELOPE = {"hello": "world"}


def test_success_reads_the_last_non_empty_line_with_a_bounded_log_read(api, executor):
    ref = executor.ensure_started(make_spec(result_limit_bytes=200))
    api.finish_succeeded(ref.name, b"starting up\n" + json.dumps(ENVELOPE).encode() + b"\n\n")
    observation = executor.observe(ref)
    assert observation.state is ObservedState.SUCCEEDED
    assert observation.result == ENVELOPE
    assert api.log_requests == [(ref.name, 201)]


def test_failure_reports_a_fixed_reason_and_no_log_text(api, executor):
    ref = executor.ensure_started(make_spec())
    api.finish_failed(ref.name, logs=b"Traceback: secret detail")
    observation = executor.observe(ref)
    assert observation.state is ObservedState.FAILED
    assert observation.reason is FailureReason.POD_FAILED
    assert observation.result is None
    assert api.log_requests == []


def test_pod_deadline_is_a_timeout(api, executor):
    ref = executor.ensure_started(make_spec())
    api.finish_deadline(ref.name)
    assert executor.observe(ref).state is ObservedState.TIMED_OUT


def test_expiry_is_a_timeout(api, executor):
    ref = executor.ensure_started(make_spec())
    api.expire(ref.name)
    assert executor.observe(ref).state is ObservedState.TIMED_OUT


def test_oversized_log_is_a_failure(api, executor):
    ref = executor.ensure_started(make_spec(result_limit_bytes=64))
    api.finish_succeeded(ref.name, b'{"pad":"' + b"x" * 200 + b'"}\n')
    observation = executor.observe(ref)
    assert observation.state is ObservedState.FAILED
    assert observation.reason is FailureReason.RESULT_TOO_LARGE


def test_exactly_the_limit_is_accepted(api, executor):
    line = b'{"a":1}'
    ref = executor.ensure_started(make_spec(result_limit_bytes=len(line)))
    api.finish_succeeded(ref.name, line)
    assert executor.observe(ref).state is ObservedState.SUCCEEDED


def test_malformed_last_line_is_a_failure(api, executor):
    ref = executor.ensure_started(make_spec())
    api.finish_succeeded(ref.name, b'{"ok": true}\nnot json at all\n')
    observation = executor.observe(ref)
    assert observation.state is ObservedState.FAILED
    assert observation.reason is FailureReason.MALFORMED_RESULT


def test_invalid_utf8_is_malformed_not_a_crash(api, executor):
    ref = executor.ensure_started(make_spec())
    api.finish_succeeded(ref.name, b"\xff\xfe\n")
    assert executor.observe(ref).reason is FailureReason.MALFORMED_RESULT


def test_empty_log_is_a_failure(api, executor):
    ref = executor.ensure_started(make_spec())
    api.finish_succeeded(ref.name, b"\n  \n")
    assert executor.observe(ref).reason is FailureReason.NO_RESULT


def test_a_vanished_sandbox_is_a_failure(api, executor):
    ref = executor.ensure_started(make_spec())
    del api.sandboxes[ref.name]
    observation = executor.observe(ref)
    assert observation.state is ObservedState.FAILED
    assert observation.reason is FailureReason.SANDBOX_GONE


def test_cancel_deletes_the_sandbox_and_its_secret_and_is_repeatable(api, executor):
    ref = executor.ensure_started(make_spec())
    executor.request_cancel(ref)
    assert api.sandboxes == {} and api.secrets == {}
    executor.request_cancel(ref)


def test_sweep_deletes_only_old_managed_sandboxes(api, executor, clock):
    old = executor.ensure_started(make_spec("old"))
    api.age(old.name, timedelta(seconds=300))
    fresh = executor.ensure_started(make_spec("fresh"))
    # Same labels, not ours: must survive.
    api.create_sandbox(
        {"metadata": {"name": "other", "labels": {"app.kubernetes.io/managed-by": "someone-else"}}, "spec": {}}
    )
    api.age("other", timedelta(days=3))
    # Longest deadline 120 s plus 60 s grace: 300 s old is stale, a fresh one is not.
    removed = executor.sweep_stale(now=clock(), longest_deadline_seconds=120)
    assert removed == [old.name]
    assert set(api.sandboxes) == {fresh.name, "other"}
    assert set(api.secrets) == {fresh.name}


def test_sweep_keeps_a_sandbox_inside_deadline_plus_grace(api, executor, clock):
    ref = executor.ensure_started(make_spec())
    api.age(ref.name, timedelta(seconds=179))
    assert executor.sweep_stale(now=clock(), longest_deadline_seconds=120) == []
    api.age(ref.name, timedelta(seconds=10))
    assert executor.sweep_stale(now=clock(), longest_deadline_seconds=120) == [ref.name]
