"""Surface test: Open WebUI's own background calls stay on the model the user chose.

The pinned Open WebUI image runs with the chart-rendered ConfigMap as its environment,
on a `docker network create --internal` network whose only other occupant is a
recording fake gateway (it answers to the chart's own gateway URL, so that URL is not
overridden).  Through Open WebUI's HTTP API the test creates the first admin, holds a
two-turn chat on the local model, and waits for the background tasks Open WebUI starts
by itself (title, tags, follow-ups).  It then asserts that every chat-completions
request the gateway received named the local model, and that background-task requests
were actually among them; otherwise "all named the local model" proves nothing.

What this does not show: it never reaches LiteLLM, so it says nothing about where
LiteLLM sends a request (that is deploy/tests/lane), and it exercises only the calls
Open WebUI makes for an ordinary chat.  Features that are off in the chart (web search,
RAG query generation, code execution) and calls only a browser triggers (autocomplete)
are not exercised.

Overrides to the chart's environment, each for a reason (see surface_harness.owui_env):
the secret-backed variables the Deployment injects (WEBUI_SECRET_KEY, OPENAI_API_KEYS),
set to dummies; OFFLINE_MODE and ENABLE_VERSION_UPDATE_CHECK, so a closed network does
not stall startup.  Local-auth mode comes from the chart's own `auth.mode=local`, not
an override.  The admin is created through POST /api/v1/auths/signup.

EXTENDING: add a SurfaceScenario to SCENARIOS with `extra_env` (more environment) or
`helm_args`.  Each scenario reruns every assertion.

Routes, from Open WebUI v0.11.3 source: POST /api/v1/auths/signup (routers/auths.py);
POST /api/chat/completions with session_id + parent_id + user_message + background_tasks
returns {task_ids, chat_id} and runs the model call and the tasks server-side (main.py,
chat_completion); GET /api/v1/chats/{id} (routers/chats.py).

Run: HEARTHAI_DOCKER_TESTS=1 pytest deploy/tests/surface -v   (needs docker and helm; skipped otherwise)
"""
import os
import time
import uuid
from dataclasses import dataclass, field

import pytest

import surface_harness as harness

ENABLED = bool(os.environ.get('HEARTHAI_DOCKER_TESTS'))
pytestmark = pytest.mark.skipif(not ENABLED, reason='set HEARTHAI_DOCKER_TESTS=1 to run the docker surface tests')

LOCAL_MODEL = 'local-qwen'
QUIET_SECONDS = 15   # no gateway traffic for this long means the tasks have finished
TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class SurfaceScenario:
    id: str
    extra_env: dict = field(default_factory=dict)
    helm_args: tuple = ()


SCENARIOS = [SurfaceScenario(id='chart-as-rendered')]


def pytest_generate_tests(metafunc):
    if 'scenario' in metafunc.fixturenames:
        metafunc.parametrize('scenario', [pytest.param(s, id=s.id) for s in SCENARIOS] if ENABLED
                             else [pytest.param(None, id='disabled')])


@pytest.fixture
def surface(scenario, tmp_path):
    stack = harness.Surface(scenario, tmp_path)
    try:
        yield stack.start()
    finally:
        if ENABLED:
            stack.stop()


def user_message(content, parent, model):
    return {'id': str(uuid.uuid4()), 'parentId': parent, 'role': 'user', 'content': content,
            'timestamp': int(time.time()), 'models': [model], 'childrenIds': []}


def send_turn(surface, token, history, text, chat_id, parent_id, tasks, stream):
    """One turn, shaped like the browser's request (Chat.svelte, v0.11.3)."""
    message = user_message(text, parent_id, LOCAL_MODEL)
    reply_id = str(uuid.uuid4())
    body = {'stream': stream, 'model': LOCAL_MODEL, 'params': {}, 'features': {}, 'variables': {},
            'messages': history + [{'role': 'user', 'content': text}],
            'session_id': 'surface-test-session', 'id': reply_id, 'parent_id': parent_id,
            'user_message': message, 'background_tasks': tasks}
    if chat_id:
        body['chat_id'] = chat_id
    reply = surface.http('POST', '/api/chat/completions', body, token=token)
    assert reply['status'] == 200 and reply['json'] and reply['json'].get('status') is True, reply
    return reply['json']['chat_id'], reply_id


def wait_for_tasks(surface, token, chat_id):
    """Until the chat has a generated title and the gateway has been quiet for QUIET_SECONDS."""
    deadline, last_count, last_change = time.time() + TIMEOUT_SECONDS, -1, time.time()
    title = None
    while time.time() < deadline:
        count = len(surface.gateway_requests())
        if count != last_count:
            last_count, last_change = count, time.time()
        chat = surface.http('GET', f'/api/v1/chats/{chat_id}', token=token)['json'] or {}
        title = chat.get('title')
        if title and title != 'New Chat' and time.time() - last_change >= QUIET_SECONDS:
            return title
        time.sleep(2)
    return title


def test_background_tasks_stay_on_the_local_model(surface):
    signup = surface.http('POST', '/api/v1/auths/signup', {
        'name': 'Admin', 'email': harness.ADMIN_EMAIL, 'password': harness.ADMIN_PASSWORD, 'profile_image_url': '/user.png'})
    assert signup['status'] == 200 and signup['json']['role'] == 'admin', signup
    token = signup['json']['token']

    models = surface.http('GET', '/api/models', token=token)
    assert models['status'] == 200, models
    assert {m['id'] for m in models['json']['data']} >= {LOCAL_MODEL, 'gpt-5.5'}, models['json']

    all_tasks = {'title_generation': True, 'tags_generation': True, 'follow_up_generation': True}
    chat_id, first_reply = send_turn(surface, token, [], 'What is a good name for a goldfish?', None, None, all_tasks, True)
    # Wait for turn one to be stored so the second turn has a real parent to attach to.
    deadline = time.time() + 60
    while time.time() < deadline:
        chat = surface.http('GET', f'/api/v1/chats/{chat_id}', token=token)['json']
        if chat['chat']['history']['messages'].get(first_reply, {}).get('done'):
            break
        time.sleep(1)
    history = [{'role': 'user', 'content': 'What is a good name for a goldfish?'},
               {'role': 'assistant', 'content': 'Canned reply from the fake gateway.'}]
    send_turn(surface, token, history, 'And for a hamster?', chat_id, first_reply,
              {'follow_up_generation': True}, False)

    title = wait_for_tasks(surface, token, chat_id)
    requests = surface.gateway_requests()
    completions = [r for r in requests if r['path'].endswith('/chat/completions')]
    kinds = [r['kind'] for r in completions]
    detail = f'title={title!r}; gateway saw: {[(r["kind"], r["model"], r["stream"]) for r in completions]}'

    print(detail)  # shown with -s or -rP: what the gateway saw
    assert 'chat' in kinds, detail
    assert any(k.startswith('task:') for k in kinds), \
        f'Open WebUI made no background-task request, so this run proves nothing. {detail}\n{surface.logs()[-3000:]}'
    assert {'task:title', 'task:tags', 'task:follow_ups'} <= set(kinds), detail
    assert [r for r in completions if r['model'] != LOCAL_MODEL] == [], detail
