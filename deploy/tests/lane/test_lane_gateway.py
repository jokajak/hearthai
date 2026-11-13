"""Runtime lane test: with the laptop down, a request for a local model reaches no cloud provider.

What it builds (all on a `docker network create --internal` network, so nothing
can leave even if routing were wrong):

  * LiteLLM (the pinned v1.106.6 image) running the chart-rendered config, plus
    ONLY a recorder callback (test harness; the chart itself must carry no
    callbacks, and the chart tests forbid it);
  * the real gate: the chart's Caddy image, Caddyfile and environment, with its
    upstream (`laptop.invalid`) unreachable;
  * a recording stand-in answering as `<fullname>-meridian:3456`, so the chart's
    own `anthropic/*` api_base lands on it with no config change;
  * a recording stand-in reached through `CHATGPT_API_BASE` for `chatgpt/*`,
    with a fake never-expiring token so a request would really be attempted.

For every local model name, each of /v1/chat/completions, /v1/responses and
/v1/messages, streaming and not, is asserted to: come back to the client as the
gate's 503 `local_unavailable` error; reach neither stand-in; and produce
recorder-callback attempts whose api_base is the gate Service and nothing else.
Positive controls send cloud model names and require the stand-ins and the
callback to see them, so "zero requests" cannot mean "recorders unreachable".

EXTENDING (router hook, personas, ...): add a `LaneScenario` to SCENARIOS.

    LaneScenario(
        id='router-hook',
        extra_config={'litellm_settings': {'callbacks': ['router_hook.hook']}},  # deep-merged
        extra_files={'router_hook.py': HOOK_SOURCE},   # mounted next to config.yaml
        model_names=('local-qwen', 'persona-grandpa'),  # None = every ollama_chat entry
        helm_args=('--set', 'someChartKey=value'),      # extra `helm template` options
    )

`extra_config` merges into the rendered config (dicts merge, lists concatenate),
so a hook's callback is added beside the recorder's.  Every assertion below then
runs again for every name in `model_names`.  A name that is not an
`ollama_chat/` entry (a persona that resolves to one) must be listed explicitly.

Run: HEARTHAI_DOCKER_TESTS=1 pytest deploy/tests/lane -v   (needs docker and helm; skipped otherwise)
"""
import json
import os
import time
from dataclasses import dataclass, field

import pytest
import yaml

import lane_harness as harness

ENABLED = bool(os.environ.get('HEARTHAI_DOCKER_TESTS'))
pytestmark = pytest.mark.skipif(not ENABLED, reason='set HEARTHAI_DOCKER_TESTS=1 to run the docker lane tests')

# Time for LiteLLM's own post-response bookkeeping; the hooks themselves run
# before the client gets its answer, so this is only a margin.
SETTLE_SECONDS = 1.0


@dataclass(frozen=True)
class LaneScenario:
    id: str
    extra_config: dict = field(default_factory=dict)
    extra_files: dict = field(default_factory=dict)
    model_names: tuple = ()
    helm_args: tuple = ()


SCENARIOS = [LaneScenario(id='chart-as-rendered')]

ENDPOINTS = ('/v1/chat/completions', '/v1/responses', '/v1/messages')


def request_body(endpoint, model, stream):
    text = [{'role': 'user', 'content': 'hello'}]
    if endpoint == '/v1/chat/completions':
        body = {'model': model, 'messages': text}
    elif endpoint == '/v1/responses':
        body = {'model': model, 'input': 'hello'}
    else:
        body = {'model': model, 'max_tokens': 16, 'messages': text}
    if stream:
        body['stream'] = True
    return body


def names_for(scenario):
    if scenario.model_names:
        return list(scenario.model_names)
    docs = harness.helm_template(*harness.LANE_OPTS, *scenario.helm_args)
    config = yaml.safe_load(harness.rendered_litellm_config_text(docs))
    names = harness.local_model_names(config)
    assert names, 'the lane is enabled but the rendered config has no ollama_chat/ entry'
    return names


def pytest_generate_tests(metafunc):
    if 'case' in metafunc.fixturenames:
        cases = []
        if ENABLED:
            for scenario in SCENARIOS:
                for name in names_for(scenario):
                    for endpoint in ENDPOINTS:
                        for stream in (False, True):
                            cases.append(pytest.param((scenario, name, endpoint, stream),
                                                      id=f'{scenario.id}-{name}-{endpoint.rsplit("/", 1)[1]}-{"stream" if stream else "plain"}'))
        else:
            cases.append(pytest.param(None, id='disabled'))
        metafunc.parametrize('case', cases)
    if 'scenario' in metafunc.fixturenames:
        metafunc.parametrize('scenario', [pytest.param(s, id=s.id) if ENABLED else pytest.param(None, id='disabled')
                                          for s in SCENARIOS])


class Stacks:
    def __init__(self, factory):
        self.factory, self.started = factory, {}

    def get(self, scenario):
        if scenario.id not in self.started:
            self.started[scenario.id] = harness.Stack(scenario, self.factory.mktemp(f'lane-{scenario.id}')).start()
        return self.started[scenario.id]


@pytest.fixture(scope='module')
def stacks(tmp_path_factory):
    holder = Stacks(tmp_path_factory)
    yield holder
    for stack in holder.started.values():
        stack.stop()


def gate_body(stack):
    """The gate's fixed error body, read from the chart-rendered Caddyfile (not retyped here)."""
    cm = next(d for d in stack.docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == stack.gate_host)
    caddyfile = cm['data']['Caddyfile']
    start = caddyfile.index('respond `') + len('respond `')
    return caddyfile[start:caddyfile.index('` 503', start)]


def test_local_name_reaches_only_the_gate(case, stacks):
    """Laptop down: 503 to the client, zero cloud requests, callback attempts only at the gate."""
    scenario, model, endpoint, stream = case
    stack = stacks.get(scenario)
    # The entry under test really does point at the gate Service (so the callback check means something).
    entries = [m for m in stack.config['model_list'] if m['model_name'] == model]
    assert entries and all(m['litellm_params']['api_base'] == stack.gate_url for m in entries), entries

    mark = stack.mark()
    reply = stack.http('POST', endpoint, request_body(endpoint, model, stream))
    time.sleep(SETTLE_SECONDS)
    seen = stack.since(mark)

    # Nothing reached a cloud provider's stand-in.
    assert seen['meridian'] == [], seen['meridian']
    assert seen['chatgpt'] == [], seen['chatgpt']

    # Where LiteLLM itself says it sent each attempt: only the gate, and at least once.
    attempts = [e for e in seen['callback'] if e['event'] == 'attempt']
    assert attempts, f'recorder callback saw no attempt; callback is not proving anything: {seen["callback"]}'
    elsewhere = [e for e in seen['callback'] if e['api_base'] != stack.gate_url]
    assert elsewhere == [], elsewhere

    # What LiteLLM v1.106.6 returns when the gate answers 503 before any byte: it does not
    # start an event stream, even when stream=true was asked.  The status is 503, the body is
    # JSON, and the gate's own body is carried verbatim inside error.message, wrapped in
    # LiteLLM's text.  /v1/messages uses the Anthropic error envelope; the other two the OpenAI one.
    assert reply['status'] == 503, reply
    assert reply['headers'].get('content-type', '').startswith('application/json'), reply['headers']
    payload = json.loads(reply['body'])
    message = payload['error']['message']
    if endpoint == '/v1/messages':
        assert payload['type'] == 'error' and payload['error']['type'] == 'api_error', payload
    else:
        assert payload['error']['code'] == '503' and payload['error']['type'] == 'internal_server_error', payload
    assert gate_body(stack) in message, message
    assert 'local_unavailable' in message


def test_cloud_models_are_recorded(scenario, stacks):
    """Positive control: the same recorders see cloud traffic, so silence above is meaningful."""
    stack = stacks.get(scenario)
    names = {m['model_name'] for m in stack.config['model_list']}
    claude = 'claude-sonnet-5' if 'claude-sonnet-5' in names else next(
        m['model_name'] for m in stack.config['model_list'] if str(m['litellm_params']['model']).startswith('anthropic/'))

    mark = stack.mark()
    reply = stack.http('POST', '/v1/chat/completions', request_body('/v1/chat/completions', claude, False))
    time.sleep(SETTLE_SECONDS)
    seen = stack.since(mark)
    assert reply['status'] == 200, reply
    assert [(e['method'], e['path'], e['model']) for e in seen['meridian']] == [('POST', '/v1/messages', claude)], seen['meridian']
    assert seen['chatgpt'] == []
    assert [e['api_base'] for e in seen['callback'] if e['event'] == 'attempt'] == [stack.cloud_url], seen['callback']

    # The chatgpt/ route is redirected by CHATGPT_API_BASE alone.  The stand-in answers 500, so
    # LiteLLM retries; what matters is that the stand-in received it.
    mark = stack.mark()
    stack.http('POST', '/v1/chat/completions', request_body('/v1/chat/completions', 'gpt-5.5', False))
    time.sleep(SETTLE_SECONDS)
    seen = stack.since(mark)
    assert seen['chatgpt'], 'chatgpt/ traffic did not reach its stand-in: the redirect or fake token failed'
    assert {(e['method'], e['model']) for e in seen['chatgpt']} == {('POST', 'gpt-5.5')}
    assert seen['meridian'] == []
    assert any(e['event'] == 'attempt' and e['provider'] == 'chatgpt' for e in seen['callback']), seen['callback']
