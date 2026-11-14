import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hearthmem.server import build_server


@pytest.fixture
def base(tmp_path):
    server = build_server(tmp_path / "data", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base, method, path, payload=None):
    request = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health(base):
    assert call(base, "GET", "/health") == (200, {"ok": True})


def test_full_round_trip(base):
    code, store = call(base, "POST", "/stores", {"purpose": "Family", "author": "alice"})
    assert code == 201
    token = store["token"]

    code, _ = call(base, "POST", f"/stores/{token}/entries",
                   {"content": "Bin day is Wednesday", "author": "alice", "tags": ["house"]})
    assert code == 201

    code, found = call(base, "GET", f"/stores/{token}/entries?q=bin")
    assert code == 200
    assert found["entries"][0]["author"] == "alice"


def test_duplicate_returns_200_not_201(base):
    _, store = call(base, "POST", "/stores", {"purpose": "Family", "author": "alice"})
    token = store["token"]
    payload = {"content": "same thing", "author": "alice"}
    assert call(base, "POST", f"/stores/{token}/entries", payload)[0] == 201
    code, body = call(base, "POST", f"/stores/{token}/entries", payload)
    assert code == 200 and body["duplicate"] is True


def test_unknown_token_is_404(base):
    code, body = call(base, "GET", "/stores/definitely-not-a-token")
    assert code == 404 and "error" in body


def test_bad_purpose_is_400(base):
    assert call(base, "POST", "/stores", {"purpose": "", "author": "a"})[0] == 400


def test_malformed_json_is_400(base):
    request = urllib.request.Request(
        base + "/stores", method="POST", data=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("should have raised")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_unknown_route_is_404(base):
    assert call(base, "GET", "/nope")[0] == 404


def test_token_via_header(base):
    _, store = call(base, "POST", "/stores", {"purpose": "Family", "author": "alice"})
    request = urllib.request.Request(
        base + "/stores/ignored", method="GET",
        headers={"X-Store-Token": store["token"]},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert json.loads(response.read())["purpose"] == "Family"


def test_sigterm_drains_instead_of_hanging(tmp_path):
    """Kubernetes sends SIGTERM before SIGKILL.

    shutdown() blocks until serve_forever() returns, so calling it directly from
    the signal handler deadlocks the main thread until the grace period expires —
    which risks SIGKILL landing mid-commit. Regression test for that.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    env = {**os.environ, "HEARTHMEM_ROOT": str(tmp_path / "data"),
           "HEARTHMEM_PORT": "8813", "HEARTHMEM_HOST": "127.0.0.1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "hearthmem.server"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:8813/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("service never became healthy")

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0, "should exit cleanly on SIGTERM"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _get(base, path, token):
    request = urllib.request.Request(base + path, method="GET", headers={"X-Store-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _send(base, method, path, token, payload=None):
    request = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-Store-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_read_token_over_http(base):
    _, store = call(base, "POST", "/stores", {"purpose": "Family", "author": "alice"})
    write = store["token"]
    assert _send(base, "POST", "/stores/-/entries", write,
                 {"content": "Bin day is Wednesday", "author": "alice"})[0] == 201

    code, minted = _send(base, "POST", "/stores/-/read-token", write)
    assert code == 201
    read = minted["read_token"]

    code, described = _get(base, "/stores/-", read)
    assert code == 200 and described["entry_count"] == 1
    assert read not in json.dumps(described) and write not in json.dumps(described)
    code, found = _get(base, "/stores/-/entries?q=bin", read)
    assert code == 200 and len(found["entries"]) == 1

    # Writes and minting are refused with a fixed message.
    code, body = _send(base, "POST", "/stores/-/entries", read,
                       {"content": "planted", "author": "mallory"})
    assert (code, body) == (403, {"error": "this token is read-only"})
    code, body = _send(base, "POST", "/stores/-/read-token", read)
    assert (code, body) == (403, {"error": "this token is read-only"})
    assert _get(base, "/stores/-", write)[1]["entry_count"] == 1

    # Rotation revokes the old token at once; the write token is unaffected.
    new = _send(base, "POST", "/stores/-/read-token", write)[1]["read_token"]
    assert _get(base, "/stores/-", read)[0] == 404
    assert _get(base, "/stores/-", new)[0] == 200
    assert _send(base, "POST", "/stores/-/entries", write,
                 {"content": "more", "author": "alice"})[0] == 201
