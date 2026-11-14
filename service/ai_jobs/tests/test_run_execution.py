"""RunService driving SandboxExecutor over the fake cluster: waiting, validation, failure, cancel."""

import json

import pytest
from conftest import Clock

from ai_jobs.cluster import FakeClusterApi
from ai_jobs.contracts import FailureCode, RunState
from ai_jobs.envelope import Envelope
from ai_jobs.profiles import noop_profile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import RunService, UnknownRun
from ai_jobs.sandbox import SandboxExecutor, object_name
from ai_jobs.storage import InMemoryRunStore
from ai_jobs.tools.noop import NoopDefinition

KEY = "123e4567-e89b-12d3-a456-426614174000"


class RecordingAudit:
    def __init__(self):
        self.events = []

    def transition(self, run, profile, failure_code=None):
        self.events.append((run.state, profile, failure_code))


class Harness:
    def __init__(self, credential=None):
        self.clock = Clock()
        self.api = FakeClusterApi(self.clock)
        self.audit = RecordingAudit()
        self.script = []  # callables run on each sleep, simulating the cluster moving on
        profile = noop_profile("registry.invalid/ai-jobs:test")
        self.runs = RunService(
            ToolRegistry((NoopDefinition(),)),
            InMemoryRunStore(),
            SandboxExecutor(self.api, clock=self.clock),
            {"noop-v1": profile},
            clock=self.clock,
            id_factory=lambda: "run-1",
            audit=self.audit,
            sleep=self._sleep,
        )

    def _sleep(self, seconds):
        self.clock.advance(seconds)
        if self.script:
            self.script.pop(0)()

    def admit(self):
        run, created = self.runs.admit("open-webui", "noop", 1, KEY, {})
        assert created
        return run

    @property
    def name(self):
        return object_name("run-1")

    def states(self):
        return [state for state, _, _ in self.audit.events]


def good_envelope():
    return json.dumps(Envelope.ok("noop", 1, {"ok": True}, "noop").to_dict()).encode() + b"\n"


@pytest.fixture
def h():
    return Harness()


def test_admit_starts_a_sandbox_with_the_canonical_request_and_records_transitions(h):
    run = h.admit()
    assert run.state is RunState.STARTING
    assert h.states() == [RunState.ACCEPTED, RunState.STARTING]
    assert h.api.created_sandboxes == 1
    assert h.api.secrets[h.name]["data"]["request.json"] == "e30="  # base64 of {}


def test_idempotent_admit_does_not_start_a_second_sandbox(h):
    first = h.admit()
    second, created = h.runs.admit("open-webui", "noop", 1, KEY, {})
    assert (second, created) == (first, False)
    assert h.api.created_sandboxes == 1


def test_success_returns_the_validated_envelope(h):
    h.admit()
    h.script = [lambda: h.api.set_running(h.name), lambda: h.api.finish_succeeded(h.name, good_envelope())]
    outcome = h.runs.await_result("run-1")
    assert outcome.failure is None
    assert isinstance(outcome.result, Envelope)
    assert outcome.result.data == {"ok": True}
    assert outcome.run.state is RunState.SUCCEEDED
    assert h.states() == [RunState.ACCEPTED, RunState.STARTING, RunState.RUNNING, RunState.SUCCEEDED]


def test_a_pod_that_finishes_before_it_is_seen_running_still_passes_through_running(h):
    h.admit()
    h.api.finish_succeeded(h.name, good_envelope())
    outcome = h.runs.await_result("run-1")
    assert outcome.run.state is RunState.SUCCEEDED
    assert RunState.RUNNING in h.states()


def test_the_outcome_is_stable_across_repeated_waits(h):
    h.admit()
    h.api.finish_succeeded(h.name, good_envelope())
    assert h.runs.await_result("run-1") is h.runs.await_result("run-1")


def test_worker_failure_is_a_fixed_internal_failure(h):
    h.admit()
    h.api.finish_failed(h.name, logs=b"Traceback: leak https://internal.example")
    outcome = h.runs.await_result("run-1")
    assert outcome.result is None
    assert outcome.failure.code is FailureCode.INTERNAL_ERROR
    assert "internal.example" not in outcome.failure.message
    assert outcome.run.state is RunState.FAILED
    assert h.audit.events[-1] == (RunState.FAILED, "noop-v1", FailureCode.INTERNAL_ERROR)


def test_pod_deadline_is_timed_out_with_a_fixed_code(h):
    h.admit()
    h.api.finish_deadline(h.name)
    outcome = h.runs.await_result("run-1")
    assert outcome.run.state is RunState.TIMED_OUT
    assert outcome.failure.code is FailureCode.DEADLINE_EXCEEDED


def test_a_sandbox_that_never_starts_times_out_at_the_profile_deadline_and_is_deleted(h):
    h.admit()
    outcome = h.runs.await_result("run-1")
    assert outcome.run.state is RunState.TIMED_OUT
    assert outcome.failure.code is FailureCode.DEADLINE_EXCEEDED
    assert h.api.sandboxes == {} and h.api.secrets == {}
    assert h.audit.events[-1][2] is FailureCode.DEADLINE_EXCEEDED


def test_timeout_waits_the_whole_deadline_and_no_more(h):
    start = h.clock.now
    h.admit()
    h.runs.await_result("run-1")
    elapsed = (h.clock.now - start).total_seconds()
    assert 10 <= elapsed < 11  # noop-v1 deadline is 10 s, polled every 0.5 s


@pytest.mark.parametrize(
    "logs",
    [
        b"definitely not json\n",
        b'{"envelope_version": 1}\n',  # parses, is not an envelope
        b"[]\n",
        json.dumps({**Envelope.ok("noop", 1, {"ok": True}, "noop").to_dict(), "tool": "web_fetch"}).encode() + b"\n",
        json.dumps({**Envelope.ok("noop", 1, {"ok": True}, "noop").to_dict(), "data": {"ok": False}}).encode() + b"\n",
        b"x" * 5000,  # over noop-v1's result limit
    ],
)
def test_bad_results_are_a_fixed_failure(h, logs):
    h.admit()
    h.api.finish_succeeded(h.name, logs)
    outcome = h.runs.await_result("run-1")
    assert outcome.result is None
    assert outcome.failure.code is FailureCode.INTERNAL_ERROR
    assert outcome.run.state is RunState.FAILED


def test_cancel_deletes_objects_and_records_cancelled(h):
    h.admit()
    run = h.runs.cancel("run-1")
    assert run.state is RunState.CANCELLED
    assert h.api.sandboxes == {} and h.api.secrets == {}
    assert h.states()[-1] is RunState.CANCELLED
    outcome = h.runs.await_result("run-1")
    assert outcome.result is None and outcome.failure is not None


def test_cancel_of_a_finished_run_leaves_it_finished(h):
    h.admit()
    h.api.finish_succeeded(h.name, good_envelope())
    h.runs.await_result("run-1")
    assert h.runs.cancel("run-1").state is RunState.SUCCEEDED


def test_unknown_run(h):
    with pytest.raises(UnknownRun):
        h.runs.await_result("nope")


def test_a_cluster_error_at_start_fails_the_run_and_is_audited():
    h = Harness()

    class Down:
        def ensure_started(self, spec):
            raise ConnectionError("api server down")

        def request_cancel(self, ref):
            pass

    h.runs.executor = Down()
    with pytest.raises(ConnectionError):
        h.runs.admit("open-webui", "noop", 1, KEY, {})
    assert h.audit.events[-1] == (RunState.FAILED, "noop-v1", FailureCode.TEMPORARILY_UNAVAILABLE)
    assert h.runs.store.get("run-1").state is RunState.FAILED


def test_observation_errors_do_not_decide_the_run(h):
    h.admit()
    real = h.runs.executor.observe
    calls = {"n": 0}

    def flaky(ref):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return real(ref)

    h.runs.executor.observe = flaky
    h.script = [lambda: h.api.finish_succeeded(h.name, good_envelope())]
    assert h.runs.await_result("run-1").run.state is RunState.SUCCEEDED


def test_a_profile_credential_is_resolved_by_the_service_and_goes_only_to_the_secret():
    from dataclasses import replace

    clock = Clock()
    api = FakeClusterApi(clock)
    profile = replace(noop_profile("registry.invalid/ai-jobs:test"), credential_source="read_token")
    runs = RunService(
        ToolRegistry((NoopDefinition(),)),
        InMemoryRunStore(),
        SandboxExecutor(api, clock=clock),
        {"noop-v1": profile},
        clock=clock,
        id_factory=lambda: "run-1",
        credential_resolver=lambda source: f"value-of-{source}".encode(),
    )
    runs.admit("open-webui", "noop", 1, KEY, {})
    import base64

    secret = api.secrets[object_name("run-1")]
    assert base64.b64decode(secret["data"]["credential"]) == b"value-of-read_token"
    assert "value-of-read_token" not in json.dumps(api.sandboxes[object_name("run-1")])


def test_a_credential_profile_without_a_resolver_fails_the_run_not_the_process():
    from dataclasses import replace

    clock = Clock()
    profile = replace(noop_profile("registry.invalid/ai-jobs:test"), credential_source="read_token")
    runs = RunService(
        ToolRegistry((NoopDefinition(),)),
        InMemoryRunStore(),
        SandboxExecutor(FakeClusterApi(clock), clock=clock),
        {"noop-v1": profile},
        clock=clock,
        id_factory=lambda: "run-1",
    )
    with pytest.raises(RuntimeError):
        runs.admit("open-webui", "noop", 1, KEY, {})
    assert runs.store.get("run-1").state is RunState.FAILED
