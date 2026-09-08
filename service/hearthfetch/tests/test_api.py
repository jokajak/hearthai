"""The tools, and the transport in front of them."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from hearthfetch.api import ToolService, build_server, page_title
from hearthfetch.classify import ClassifierConfig
from hearthfetch.config import Config
from hearthfetch.distill import DistillerConfig
from hearthfetch.fetch import RawResponse
from hearthfetch.handles import mint, new_key
from hearthfetch.llm import ModelConfig
from hearthfetch.observability import Metrics
from hearthfetch.pipeline import Pipeline
from hearthfetch.search import RawResult

from fakes import ScriptedModel

NOW = 1_000_000.0
HTML = {"Content-Type": "text/html; charset=utf-8"}
PAGE = b"<html><head><title>Bridge news</title></head><body><p>Reopened Tuesday.</p></body></html>"
TOKEN = "a-long-tool-token"

CONFIG = Config(
    distiller=DistillerConfig(model=ModelConfig("fake-distiller", 800, 30.0)),
    classifier=ClassifierConfig(model=ModelConfig("fake-classifier", 8, 15.0)),
    litellm_base_url="http://litellm.example/v1",
)


class StubProvider:
    def __init__(self, results):
        self.results = results

    def search(self, query, count):
        return self.results[:count]


def make_service(*, results=None, response=None, distiller=None, classifier=None, key=None):
    return ToolService(
        config=CONFIG,
        pipeline=Pipeline(
            config=CONFIG,
            distiller_model=distiller or ScriptedModel("The bridge reopened Tuesday."),
            classifier_model=classifier or ScriptedModel("clean"),
        ),
        search_provider=StubProvider(
            results if results is not None else [RawResult("https://a.example/1", "A", "s")]
        ),
        handle_key=key or new_key(),
        resolver=lambda host, port: ["93.184.216.34"],
        connector=lambda target, address, *, timeout: response
        or RawResponse(200, HTML, PAGE),
        metrics=Metrics(),
        clock=lambda: NOW,
    )


# ------------------------------------------------------------------ search_web

def test_search_returns_handles_and_no_urls():
    code, payload = make_service().search_web({"query": "bridge"})
    assert code == 200
    assert "a.example" not in json.dumps(payload)
    assert payload["results"][0]["handle"].startswith("hf1.")


def test_search_rejects_an_infrastructure_shaped_field():
    code, payload = make_service().search_web({"query": "x", "model": "gpt-5.4-mini"})
    assert code == 400
    assert payload["error"]["code"] == "invalid_request"


def test_search_counts_an_empty_result_set_separately():
    service = make_service(results=[])
    service.search_web({"query": "x"})
    assert service.metrics.value("hearthfetch_tool_calls", tool="search_web", outcome="empty") == 1


# --------------------------------------------------------------- fetch_result

def test_fetch_result_round_trip():
    key = new_key()
    service = make_service(key=key)
    handle = mint("https://a.example/1", key=key, search_id="s", now=NOW)
    code, payload = service.fetch_result({"handle": handle, "question": "when?"})
    assert code == 200
    assert payload["content"] == "The bridge reopened Tuesday."
    assert payload["title"] == "Bridge news"
    assert "url" not in payload


@pytest.mark.parametrize("handle", ["garbage", "hf1.a.b", ""])
def test_a_bad_handle_is_not_distinguishable_from_a_bad_request(handle):
    """Forged, expired and malformed must not be separable by a prober."""
    code, payload = make_service().fetch_result({"handle": handle or "x", "question": "q"})
    assert code == 400
    assert payload["error"]["code"] == "invalid_request"


def test_an_expired_handle_is_refused():
    key = new_key()
    service = make_service(key=key)
    stale = mint("https://a.example/1", key=key, search_id="s", now=NOW - 10_000, ttl_seconds=60)
    code, _ = service.fetch_result({"handle": stale, "question": "q"})
    assert code == 400


def test_a_rejected_source_never_says_which_stage_rejected_it():
    key = new_key()
    service = make_service(key=key, classifier=ScriptedModel("side_effect"))
    handle = mint("https://a.example/1", key=key, search_id="s", now=NOW)
    code, payload = service.fetch_result({"handle": handle, "question": "q"})
    assert code == 200
    assert payload["error"] == {"code": "source_rejected", "message": "source rejected"}
    # The stage is an operator metric, not a caller-visible field.
    assert service.metrics.value("hearthfetch_source_rejections", stage="classify") == 1


def test_a_blocked_destination_is_reported_without_naming_it():
    key = new_key()
    service = ToolService(
        config=CONFIG,
        pipeline=Pipeline(CONFIG, ScriptedModel(), ScriptedModel("clean")),
        search_provider=StubProvider([]),
        handle_key=key,
        resolver=lambda host, port: ["10.0.0.7"],
        connector=lambda *a, **k: RawResponse(200, HTML, PAGE),
        metrics=Metrics(),
        clock=lambda: NOW,
    )
    handle = mint("https://internal.example/x", key=key, search_id="s", now=NOW)
    code, payload = service.fetch_result({"handle": handle, "question": "q"})
    assert code == 200
    assert payload["error"]["code"] == "source_rejected"
    assert "10.0.0.7" not in json.dumps(payload)
    assert service.metrics.value("hearthfetch_fetch_refusals", reason="blocked_address") == 1


# ------------------------------------------------------------------- title

def test_a_page_title_is_scrubbed_like_any_other_attacker_text():
    assert page_title("<title>Normal title</title>") == "Normal title"
    assert page_title("<title>Go to https://evil.example/x</title>") == ""
    assert page_title("<html><body>no title</body></html>") == ""


# -------------------------------------------------------------- HTTP surface

@pytest.fixture
def server():
    service = make_service()
    srv = build_server(service, token=TOKEN, openapi={"openapi": "3.1.0"}, port=0)
    # Tight poll interval: shutdown() waits for the next tick, and the default
    # 0.5s would put four seconds of teardown into this file alone.
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield srv, service, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(base, path, body, *, token=TOKEN, content_type="application/json"):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    request = urllib.request.Request(f"{base}{path}", data=data, method="POST")
    if content_type:
        request.add_header("Content-Type", content_type)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health_and_readiness_are_distinct(server):
    _, _, base = server
    with urllib.request.urlopen(f"{base}/healthz", timeout=5) as r:
        assert r.status == 200
    with urllib.request.urlopen(f"{base}/readyz", timeout=5) as r:
        assert r.status == 200


def test_readiness_fails_when_there_is_nowhere_to_send_a_distillation():
    service = ToolService(
        config=Config(distiller=CONFIG.distiller, classifier=CONFIG.classifier),
        pipeline=Pipeline(CONFIG, ScriptedModel(), ScriptedModel("clean")),
        search_provider=StubProvider([]),
        handle_key=new_key(),
        resolver=lambda h, p: ["93.184.216.34"],
        connector=lambda *a, **k: RawResponse(200, HTML, PAGE),
    )
    srv = build_server(service, token=TOKEN, openapi={}, port=0)
    threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/readyz", timeout=5)
        assert caught.value.code == 503
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("token", [None, "", "wrong-token", TOKEN[:-1], TOKEN + "x"])
def test_a_wrong_or_missing_bearer_is_refused(server, token):
    _, _, base = server
    code, _ = post(base, "/v1/tools/search_web", {"query": "x"}, token=token)
    assert code == 401


def test_auth_is_checked_before_the_body_is_read(server):
    """An unauthenticated caller must not be able to make us parse anything."""
    _, service, base = server
    code, _ = post(base, "/v1/tools/search_web", {"query": "x"}, token="wrong")
    assert code == 401
    assert service.metrics.counters == {}


def test_a_non_json_content_type_is_refused(server):
    _, _, base = server
    code, _ = post(base, "/v1/tools/search_web", b'{"query":"x"}', content_type="text/plain")
    assert code == 415


def test_an_oversized_body_is_refused(server):
    _, _, base = server
    code, _ = post(base, "/v1/tools/search_web", {"query": "x" * 200_000})
    assert code == 413


def test_malformed_json_is_refused(server):
    _, _, base = server
    code, payload = post(base, "/v1/tools/search_web", b"{not json")
    assert code == 400
    assert payload["error"]["code"] == "invalid_request"


def test_unknown_routes_are_not_found(server):
    _, _, base = server
    assert post(base, "/v1/tools/fetch_url", {"url": "https://a.example"})[0] == 404
    assert post(base, "/jobs", {})[0] == 404


def test_the_openapi_document_is_served_without_a_token(server):
    _, _, base = server
    with urllib.request.urlopen(f"{base}/openapi.json", timeout=5) as r:
        assert json.loads(r.read())["openapi"] == "3.1.0"


def test_metrics_are_exposed_in_prometheus_format(server):
    _, _, base = server
    post(base, "/v1/tools/search_web", {"query": "x"})
    with urllib.request.urlopen(f"{base}/metrics", timeout=5) as r:
        body = r.read().decode()
    assert "hearthfetch_tool_calls" in body
    # And carry nothing from the request.
    assert "query" not in body and "a.example" not in body


def test_an_unexpected_error_returns_a_stable_failure_not_a_traceback(server):
    srv, service, base = server
    object.__setattr__(service, "search_provider", None)  # provoke an AttributeError
    code, payload = post(base, "/v1/tools/search_web", {"query": "x"})
    assert code == 500
    assert payload == {"error": {"code": "internal_error", "message": "internal error"}}
