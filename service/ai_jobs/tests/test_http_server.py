"""``ai-jobs serve``: auth, admission errors and a recall end to end.

The end-to-end path is real except the cluster: a real hearthmem ``build_server``, the real
``RunService``, ``SandboxExecutor`` and worker code, with a fake cluster that runs the worker
in-process from the per-run Secret and the container command the Sandbox carries.
"""

import json
import threading
import urllib.error
import urllib.request
from itertools import count

import pytest
from conftest import Clock
from recall_support import Hearthmem, StubHearthmem, WorkerRunningCluster, serve_in_thread

from ai_jobs.contracts import RunState
from ai_jobs.openapi import openapi_document
from ai_jobs.profiles import memory_recall_profile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import RunService
from ai_jobs.sandbox import SandboxExecutor, object_name
from ai_jobs.server import App, CallerKeys, ConfigError, build_server, credential_resolver
from ai_jobs.storage import InMemoryRunStore
from ai_jobs.tools.memory_recall import MemoryRecallDefinition

KEY_WEBUI = "webui-" + "k" * 40
KEY_N8N = "n8n-" + "z" * 40
CALLERS = {KEY_WEBUI: "open-webui", KEY_N8N: "n8n"}
ROUTE = "/v1/tools/memory-recall"

ENTRIES = [
    {"content": "The boiler reset code is on the fridge.", "author": "sam", "tags": ["home"]},
    {"content": "Bins go out on Tuesday nights.", "author": "alex", "tags": ["home"]},
]


class RecordingAudit:
    def __init__(self):
        self.events = []

    def transition(self, run, profile, failure_code=None):
        self.events.append((run.caller_id, run.state, profile, failure_code))


class Stack:
    def __init__(self, tmp_path, hearthmem_url, read_token, *, clock=None, sleep=None, max_concurrent_runs=4):
        self.clock = clock or Clock()
        self.cluster = WorkerRunningCluster(tmp_path / "pods", self.clock)
        (tmp_path / "pods").mkdir()
        self.audit = RecordingAudit()
        self.credential_calls = []
        ids = count(1)

        def resolve(source):
            self.credential_calls.append(source)
            return read_token.encode()

        self.runs = RunService(
            ToolRegistry((MemoryRecallDefinition(),)),
            InMemoryRunStore(),
            SandboxExecutor(self.cluster, clock=self.clock),
            {"memory-recall-v1": memory_recall_profile("registry.invalid/ai-jobs:test", hearthmem_url)},
            clock=self.clock,
            id_factory=lambda: f"run-{next(ids)}",
            audit=self.audit,
            credential_resolver=resolve,
            sleep=sleep or (lambda seconds: self.clock.advance(seconds)),
            poll_seconds=0.01,
        )
        self.app = App(self.runs, CallerKeys.from_mapping(CALLERS), max_concurrent_runs=max_concurrent_runs)
        self.app.ready.set()
        self.server = build_server(self.app, port=0)
        self.url = serve_in_thread(self.server)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, body, key=KEY_WEBUI, headers=None, raw=None):
        return post(self.url, body, key, headers, raw)

    def cluster_text(self):
        return json.dumps([self.cluster.sandboxes, self.cluster.secrets], sort_keys=True)


def post(base, body, key=KEY_WEBUI, headers=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    request_headers = {"Content-Type": "application/json"}
    if key is not None:
        request_headers["Authorization"] = f"Bearer {key}"
    request_headers.update(headers or {})
    return fetch(urllib.request.Request(base + ROUTE, data=data, method="POST", headers=request_headers))


def fetch(request):
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.fixture(scope="module")
def hearthmem(tmp_path_factory):
    """Seeded once; no test here writes to it."""
    hm = Hearthmem(tmp_path_factory.mktemp("hearthmem"), entries=ENTRIES)
    yield hm
    hm.close()


@pytest.fixture
def stack(tmp_path, hearthmem):
    s = Stack(tmp_path, hearthmem.url, hearthmem.read_token)
    yield s
    s.close()


# ---- probes, document -----------------------------------------------------------


def test_probes_and_document_need_no_key(stack):
    assert fetch(urllib.request.Request(stack.url + "/healthz"))[::2] == (200, b'{"ok":true}')
    assert fetch(urllib.request.Request(stack.url + "/readyz"))[::2] == (200, b'{"ready":true}')
    status, headers, body = fetch(urllib.request.Request(stack.url + "/openapi.json"))
    assert status == 200 and json.loads(body) == openapi_document()
    assert headers["Content-Type"] == "application/json"


def test_not_ready_until_started(stack):
    stack.app.ready.clear()
    assert fetch(urllib.request.Request(stack.url + "/readyz"))[0] == 503


def test_unknown_routes_and_wrong_methods(stack):
    assert fetch(urllib.request.Request(stack.url + "/v1/tools/web-fetch"))[0] == 404
    assert fetch(urllib.request.Request(stack.url + "/v1/tools/web-fetch", data=b"{}", method="POST"))[0] == 404
    assert fetch(urllib.request.Request(stack.url + ROUTE))[0:2][0] == 405
    assert fetch(urllib.request.Request(stack.url + "/", method="POST", data=b"{}"))[0] == 404


# ---- authentication ----------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-" + "x" * 40},
        {"Authorization": "Basic " + KEY_WEBUI},
        {"Authorization": KEY_WEBUI},
        {"Authorization": "Bearer " + KEY_WEBUI + "x"},
        {"Authorization": "Bearer " + KEY_WEBUI[:-1]},
        {"X-Api-Key": KEY_WEBUI},
    ],
)
def test_a_missing_or_unknown_key_is_401_and_creates_nothing(stack, headers):
    request = urllib.request.Request(
        stack.url + ROUTE, data=json.dumps({"query": "boiler"}).encode(), method="POST", headers=headers
    )
    status, response_headers, body = fetch(request)
    assert status == 401
    assert response_headers["WWW-Authenticate"] == "Bearer"
    assert json.loads(body)["code"] == "invalid_request"
    assert KEY_WEBUI not in body.decode()
    assert stack.cluster.created_sandboxes == 0 and stack.cluster.created_secrets == 0
    assert stack.credential_calls == []


def test_authentication_comes_before_the_body_is_looked_at(stack):
    # Invalid JSON and an oversized body from an unauthenticated caller are still just 401.
    assert stack.post(None, key=None, raw=b"{not json")[0] == 401
    assert stack.post(None, key="nope" * 10, raw=b"x" * 10_000)[0] == 401


def test_each_key_maps_to_its_caller_in_the_audit(stack):
    assert stack.post({"query": "boiler"}, key=KEY_WEBUI)[0] == 200
    assert stack.post({"query": "bins"}, key=KEY_N8N)[0] == 200
    callers = {caller for caller, *_ in stack.audit.events}
    assert callers == {"open-webui", "n8n"}


# ---- a recall end to end -----------------------------------------------------------------


def test_recall_end_to_end(stack, hearthmem):
    status, headers, body = stack.post({"query": "boiler", "limit": 3})
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    envelope = MemoryRecallDefinition().validate_result(json.loads(body))
    assert envelope.status.value == "ok" and envelope.trust.value == "household_shared"
    [entry] = envelope.data["entries"]
    assert entry["content"] == "The boiler reset code is on the fridge."
    assert entry["author"] == "sam"

    # the run went through the lifecycle and the audit saw each step
    states = [state for _, state, *_ in stack.audit.events]
    assert states == [RunState.ACCEPTED, RunState.STARTING, RunState.RUNNING, RunState.SUCCEEDED]
    assert {profile for _, _, profile, _ in stack.audit.events} == {"memory-recall-v1"}

    # one Sandbox, and one Secret holding the canonical request and the READ token only
    assert stack.cluster.created_sandboxes == 1
    secret = stack.cluster.secrets[object_name("run-1")]["data"]
    import base64

    assert json.loads(base64.b64decode(secret["request.json"])) == {"limit": 3, "query": "boiler"}
    assert base64.b64decode(secret["credential"]).decode() == hearthmem.read_token
    assert stack.credential_calls == ["memory_recall_read_token"]
    assert hearthmem.write_token not in stack.cluster_text()

    # the Sandbox itself holds neither the query nor any token
    sandbox = json.dumps(stack.cluster.sandboxes[object_name("run-1")])
    assert "boiler" not in sandbox and hearthmem.read_token not in sandbox
    # and the run record keeps a digest, not the query
    stored = stack.runs.store.get("run-1").request
    assert set(stored) == {"query_digest", "limit"} and "boiler" not in json.dumps(stored)


def test_an_empty_store_match_is_an_ok_empty_result(stack):
    status, _, body = stack.post({"query": "zzzqqq"})
    assert status == 200 and json.loads(body)["data"] == {"entries": []}


def test_limit_default_reaches_the_worker(stack):
    stack.post({"query": "home"})
    secret = stack.cluster.secrets[object_name("run-1")]["data"]
    import base64

    assert json.loads(base64.b64decode(secret["request.json"]))["limit"] == 5


def test_a_replayed_idempotency_key_shares_one_run(stack):
    key = "123e4567-e89b-12d3-a456-426614174000"
    first = stack.post({"query": "boiler"}, headers={"Idempotency-Key": key})
    second = stack.post({"query": "boiler"}, headers={"Idempotency-Key": key})
    assert first[0] == second[0] == 200 and first[2] == second[2]
    assert stack.cluster.created_sandboxes == 1


def test_the_same_key_with_different_content_is_409(stack):
    key = "123e4567-e89b-12d3-a456-426614174000"
    assert stack.post({"query": "boiler"}, headers={"Idempotency-Key": key})[0] == 200
    status, _, body = stack.post({"query": "bins"}, headers={"Idempotency-Key": key})
    assert status == 409 and json.loads(body)["code"] == "invalid_request"
    assert stack.cluster.created_sandboxes == 1


def test_keys_are_per_caller(stack):
    key = "123e4567-e89b-12d3-a456-426614174000"
    assert stack.post({"query": "boiler"}, key=KEY_WEBUI, headers={"Idempotency-Key": key})[0] == 200
    assert stack.post({"query": "bins"}, key=KEY_N8N, headers={"Idempotency-Key": key})[0] == 200
    assert stack.cluster.created_sandboxes == 2


# ---- admission errors: nothing is created ------------------------------------------------------


@pytest.mark.parametrize(
    "body, message",
    [
        ({}, "missing: query"),
        ({"query": ""}, "empty"),
        ({"query": "x" * 501}, "500"),
        ({"query": "q", "limit": 0}, "between 1 and 10"),
        ({"query": "q", "limit": 11}, "between 1 and 10"),
        ({"query": "q", "limit": "5"}, "integer"),
        ({"query": "q", "image": "evil:latest"}, "unknown fields: image"),
        ({"query": "q", "command": ["sh"]}, "unknown fields: command"),
        ({"query": "q", "namespace": "kube-system"}, "unknown fields: namespace"),
        ([], "object"),
        ("text", "object"),
        (None, "object"),
    ],
)
def test_an_invalid_request_is_400_and_nothing_reaches_the_cluster(stack, body, message):
    status, _, response = stack.post(body)
    assert status == 400
    failure = json.loads(response)
    assert failure["code"] == "invalid_request" and message in failure["message"]
    assert set(failure) == {"code", "message"}
    assert stack.cluster.created_sandboxes == 0 and stack.cluster.created_secrets == 0
    assert stack.credential_calls == []


@pytest.mark.parametrize(
    "raw, status",
    [
        (b"", 400),
        (b"{not json", 400),
        (b"\xff\xfe", 400),
        (b'{"query": "' + b"x" * 5_000 + b'"}', 413),
    ],
)
def test_malformed_and_oversized_bodies(stack, raw, status):
    assert stack.post(None, raw=raw)[0] == status
    assert stack.cluster.created_sandboxes == 0


def test_a_bad_idempotency_key_is_400(stack):
    status, _, body = stack.post({"query": "q"}, headers={"Idempotency-Key": "not-a-uuid"})
    assert status == 400 and "UUID" in json.loads(body)["message"]
    assert stack.cluster.created_sandboxes == 0


# ---- failures are fixed -------------------------------------------------------------------------


def test_a_cluster_that_cannot_start_the_run_is_503_with_a_fixed_message(stack):
    def explode(manifest):
        raise RuntimeError("boom: internal-cluster-detail.example")

    stack.cluster.create_sandbox = explode
    status, _, body = stack.post({"query": "boiler"})
    assert status == 503
    assert json.loads(body) == {"code": "temporarily_unavailable", "message": "The tool is unavailable."}
    assert "boom" not in body.decode()


def test_a_credential_that_cannot_be_resolved_is_503(tmp_path, hearthmem):
    s = Stack(tmp_path, hearthmem.url, hearthmem.read_token)
    try:
        def broken(source):
            raise OSError("/run/secrets/nope: permission denied")

        s.runs.credential_resolver = broken
        status, _, body = s.post({"query": "boiler"})
        assert status == 503 and "permission" not in body.decode()
        assert s.cluster.created_sandboxes == 0
    finally:
        s.close()


def test_hearthmem_text_never_reaches_the_caller(tmp_path):
    stub = StubHearthmem(status=500, body=b"Traceback: secret-host.internal password=hunter2")
    s = Stack(tmp_path, stub.url, "r" * 43)
    try:
        status, _, body = s.post({"query": "boiler"})
    finally:
        s.close()
        stub.close()
    assert status == 200  # the run succeeded; its envelope reports the failure
    envelope = json.loads(body)
    assert envelope["status"] == "error" and envelope["error"]["code"] == "fetch_failed"
    assert envelope["data"] is None and envelope["trust"] == "household_shared"
    for leaked in ("Traceback", "secret-host", "hunter2"):
        assert leaked not in body.decode()


def test_the_real_hearthmem_refusing_the_token_is_a_fixed_envelope(tmp_path, hearthmem):
    s = Stack(tmp_path, hearthmem.url, "w" * 43)  # a token no store knows
    try:
        status, _, body = s.post({"query": "boiler"})
    finally:
        s.close()
    assert status == 200 and json.loads(body)["error"]["code"] == "fetch_failed"
    assert "no store matches" not in body.decode()


def test_a_worker_that_prints_garbage_is_500_internal_error(tmp_path, hearthmem):
    s = Stack(tmp_path, hearthmem.url, hearthmem.read_token)
    try:
        s.cluster.auto_run = False
        s.runs.sleep = lambda seconds: (
            s.clock.advance(seconds),
            s.cluster.finish_succeeded(object_name("run-1"), b"Traceback: leak\nnot an envelope\n"),
        )
        status, _, body = s.post({"query": "boiler"})
    finally:
        s.close()
    assert status == 500
    assert json.loads(body) == {"code": "internal_error", "message": "The tool could not complete this request."}


def test_a_run_that_overruns_its_deadline_is_504_and_its_sandbox_is_deleted(tmp_path, hearthmem):
    s = Stack(tmp_path, hearthmem.url, hearthmem.read_token)
    try:
        s.cluster.auto_run = False
        status, _, body = s.post({"query": "boiler"})
        assert status == 504
        assert json.loads(body)["code"] == "deadline_exceeded"
        assert s.cluster.deleted == [object_name("run-1")]
        assert s.cluster.sandboxes == {} and s.cluster.secrets == {}
    finally:
        s.close()


def test_a_busy_server_refuses_with_503_and_retry_after(tmp_path, hearthmem):
    gate = threading.Event()
    clock = Clock()

    def sleep(seconds):
        gate.wait(10)
        clock.advance(100)

    s = Stack(tmp_path, hearthmem.url, hearthmem.read_token, clock=clock, sleep=sleep, max_concurrent_runs=1)
    try:
        s.cluster.auto_run = False
        first = {}
        thread = threading.Thread(target=lambda: first.update(result=s.post({"query": "boiler"})))
        thread.start()
        for _ in range(500):
            if s.cluster.created_sandboxes:
                break
            threading.Event().wait(0.01)
        status, headers, body = s.post({"query": "bins"})
        assert status == 503 and headers["Retry-After"] == "5"
        assert json.loads(body)["code"] == "temporarily_unavailable"
        assert s.cluster.created_sandboxes == 1  # the refused request created nothing
        gate.set()
        thread.join(10)
        assert first["result"][0] == 504
        # the slot is free again
        s.cluster.auto_run = True
        s.runs.sleep = lambda seconds: clock.advance(seconds)
        assert s.post({"query": "bins"})[0] == 200
    finally:
        gate.set()
        s.close()


# ---- configuration -----------------------------------------------------------------------------------


def test_caller_keys_load_from_a_mounted_file(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(CALLERS))
    keys = CallerKeys.from_file(path)
    assert keys.caller_for(KEY_WEBUI) == "open-webui"
    assert keys.caller_for(KEY_N8N) == "n8n"
    assert keys.caller_for(KEY_N8N + " ") is None
    assert keys.caller_for("") is None
    assert keys.caller_for("\udcff") is None  # not encodable: still just unknown


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "{}",
        "[]",
        "not json",
        json.dumps({"short": "open-webui"}),
        json.dumps({KEY_WEBUI: "Open WebUI"}),
        json.dumps({KEY_WEBUI: ""}),
        json.dumps({KEY_WEBUI: 5}),
        json.dumps({" " + KEY_WEBUI: "open-webui"}),
        json.dumps({"x" * 70_000: "a"}),
    ],
)
def test_unusable_key_files_fail_startup_without_echoing_a_key(tmp_path, content):
    path = tmp_path / "keys.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(ConfigError) as error:
        CallerKeys.from_file(path)
    assert KEY_WEBUI not in str(error.value)


def test_credential_resolver_reads_a_named_file_and_nothing_else(tmp_path):
    (tmp_path / "memory_recall_read_token").write_text("abc\n")
    (tmp_path / "empty").write_text("\n")
    resolve = credential_resolver(tmp_path)
    assert resolve("memory_recall_read_token") == b"abc"
    for bad in ("../etc/passwd", "a/b", "", "UPPER", "empty", "missing"):
        with pytest.raises((ValueError, OSError)):
            resolve(bad)
