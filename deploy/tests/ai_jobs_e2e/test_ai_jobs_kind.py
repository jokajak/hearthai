"""ai-jobs on a real API server (kind): lifecycle, admission and cleanup. NOT isolation.

kind's CNI enforces no CiliumNetworkPolicy and kind has no gVisor, so nothing here shows that a
sandbox pod is contained or runs under a stronger runtime. That is the owner's check on the real
cluster. What these tests do show, against the real agent-sandbox v1.0.9 controller and the real
control-plane image:

  * the chart installs, and the control plane starts with the files and environment it gives it;
  * a no-op run and a recall run each become one Sandbox and one per-run Secret in a namespace
    enforcing Pod Security `restricted`, the worker (uid 65532, read-only root) can read its
    Secret, and the envelope comes back through pods/log;
  * the Sandbox, its Secret and its pod are gone after shutdownTime;
  * a request with an extra field is refused before anything is created;
  * the startup sweep deletes stale ai-jobs Sandboxes and leaves fresh and foreign ones.

Run: HEARTHAI_KIND_TESTS=1 pytest deploy/tests/ai_jobs_e2e -v -s   (needs docker, kind, kubectl, helm)
"""
import json
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from e2e_harness import AI_JOBS_IMAGE, ENABLED, NAME, NAMESPACE, RELEASE, SANDBOX_NAMESPACE, Cluster

pytestmark = pytest.mark.skipif(not ENABLED, reason='set HEARTHAI_KIND_TESTS=1 to run the kind end-to-end test')

# 15 s profile deadline + 60 s grace, plus the controller's reconcile latency.
SHUTDOWN_WAIT_SECONDS = 150


@pytest.fixture(scope='session')
def cluster():
    with tempfile.TemporaryDirectory(prefix=f'{NAME}-') as tmp:
        c = Cluster(Path(tmp))
        try:
            c.up()
        except BaseException:
            try:
                c.diagnostics()
            finally:
                c.down()
            raise
        yield c
        c.down()


def post(port, path, body, key, headers=None):
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}', method='POST', data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', **({'Authorization': f'Bearer {key}'} if key else {}),
                 **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


@pytest.fixture(scope='module')
def control_plane(cluster):
    return cluster.port_forward(f'svc/{RELEASE}-ai-jobs', 8080)


def test_agent_sandbox_core_is_installed(cluster):
    controller = cluster.kjson('-n', 'agent-sandbox-system', 'get', 'deploy/agent-sandbox-controller')
    image = controller['spec']['template']['spec']['containers'][0]['image']
    print(f'[e2e] controller image: {image}')
    assert image.endswith(':v1.0.9')
    assert '--extensions' not in controller['spec']['template']['spec']['containers'][0].get('args', [])
    crds = cluster.kubectl('get', 'crd', '-o', 'name')
    assert 'sandboxes.agents.x-k8s.io' in crds
    assert 'sandboxclaims' not in crds and 'sandboxtemplates' not in crds  # core only


def test_chart_objects_are_live(cluster):
    labels = cluster.kjson('get', 'ns', SANDBOX_NAMESPACE)['metadata']['labels']
    assert labels['pod-security.kubernetes.io/enforce'] == 'restricted'
    ready = cluster.kjson('-n', NAMESPACE, 'get', f'deploy/{RELEASE}-ai-jobs')['status']
    assert ready['readyReplicas'] == 1
    policies = {p['metadata']['name'] for p in
                cluster.kjson('get', 'ciliumnetworkpolicies.cilium.io', '-A')['items']}
    print(f'[e2e] CiliumNetworkPolicies admitted (not enforced by kind): {sorted(policies)}')
    assert {'default-deny', f'{RELEASE}-ai-jobs', f'{RELEASE}-ai-jobs-memory-recall-v1'} <= policies
    # The sandbox namespace starts empty.
    assert all(not names for names in cluster.objects_in_sandbox_namespace().values())


def test_control_plane_pod_as_deployed(cluster):
    pod = cluster.kjson('-n', NAMESPACE, 'get', 'pods', '-l', 'app.kubernetes.io/name=ai-jobs')['items'][0]
    spec = pod['spec']
    assert spec['serviceAccountName'] == f'{RELEASE}-ai-jobs'
    assert any(v['name'].startswith('kube-api-access') for v in spec['volumes'])  # its token is mounted
    assert spec['containers'][0]['securityContext']['readOnlyRootFilesystem'] is True
    user = cluster.kubectl('-n', NAMESPACE, 'exec', pod['metadata']['name'], '--', 'id', '-u',
                           check=False).strip()
    print(f'[e2e] control plane runs as uid {user or "(no id binary in the image; see securityContext)"}')
    # The caller keys file is readable by the non-root process through fsGroup.
    listing = cluster.kubectl('-n', NAMESPACE, 'exec', pod['metadata']['name'], '--', 'python3', '-c',
                              'import os,stat;'
                              'print([(p, oct(os.stat(p).st_mode & 0o777), os.access(p, os.R_OK)) for p in '
                              '("/etc/ai-jobs/callers/caller-keys.json","/run/ai-jobs/credentials/memory_recall_read_token")])')
    print(f'[e2e] mounted secret files (path, mode, readable by the process): {listing.strip()}')
    assert listing.count('True') == 2
    # Only the two keys ai-jobs needs are projected; the raw Open WebUI key is not in the pod.
    for volume in spec['volumes']:
        if 'secret' in volume:
            assert 'caller_key_open_webui' not in json.dumps(volume)


NOOP_SCRIPT = '''
import json, os
from ai_jobs.cluster import KubernetesClusterApi
from ai_jobs.profiles import noop_profile
from ai_jobs.registry import ToolRegistry
from ai_jobs.runs import RunService
from ai_jobs.sandbox import SandboxExecutor
from ai_jobs.storage import InMemoryRunStore
from ai_jobs.tools.noop import NoopDefinition
profile = noop_profile(os.environ["AI_JOBS_IMAGE"])
runs = RunService(ToolRegistry((NoopDefinition(),)), InMemoryRunStore(),
                  SandboxExecutor(KubernetesClusterApi(os.environ["AI_JOBS_NAMESPACE"])), {profile.name: profile})
run, _ = runs.admit("e2e-client", "noop", 1, "6f1d3c52-8a0e-4b3f-9d2a-0c5e7b1a9e11", {})
outcome = runs.await_result(run.id)
print(json.dumps({"run": run.id, "failure": outcome.failure and outcome.failure.to_dict(),
                  "result": outcome.result and outcome.result.to_dict()}))
'''


def test_noop_run_through_the_executor(cluster):
    """The noop tool is deliberately not model-callable, so it has no HTTP route: this drives the
    same RunService, SandboxExecutor and in-cluster client the HTTP surface uses, from inside the
    control-plane pod (its service account, its image, its environment)."""
    out = cluster.kubectl('-n', NAMESPACE, 'exec', f'deploy/{RELEASE}-ai-jobs', '--', 'python3', '-c',
                          NOOP_SCRIPT, timeout=120)
    outcome = json.loads(out.strip().splitlines()[-1])
    print(f'[e2e] noop outcome: {json.dumps(outcome)}')
    assert outcome['failure'] is None
    assert outcome['result']['status'] == 'ok' and outcome['result']['data'] == {'ok': True}
    sandbox, = [s for s in cluster.sandboxes() if s['metadata']['labels']['ai-jobs.hearthai/profile'] == 'noop-v1']
    conditions = {c['type']: c for c in sandbox['status']['conditions']}
    assert conditions['Finished']['status'] == 'True' and conditions['Finished']['reason'] == 'PodSucceeded'
    wait_until_gone(cluster, sandbox['metadata']['name'])


def wait_until_gone(cluster, name):
    """Wait for the Sandbox, its Secret and its pod to be gone; return seconds taken."""
    started = time.time()
    cluster.wait_for(f'{name} and its Secret and pod to be deleted',
                     lambda: all(name not in names for names in cluster.objects_in_sandbox_namespace().values()),
                     SHUTDOWN_WAIT_SECONDS, interval=3)
    return time.time() - started


def test_recall_run_through_the_http_api(cluster, control_plane):
    before = cluster.objects_in_sandbox_namespace()
    assert all(not names for names in before.values()), before

    status, body = post(control_plane, '/v1/tools/memory-recall', {'query': 'recycling', 'limit': 5}, cluster.caller_key)
    print(f'[e2e] recall response: {status} {json.dumps(body)}')
    assert status == 200
    assert body['status'] == 'ok' and body['trust'] == 'household_shared'
    contents = [e['content'] for e in body['data']['entries']]
    assert contents == ['Recycling goes out on Thursday.']  # the other entry does not match the query
    # The envelope is all the caller gets: no pod name, no log text, no token.
    assert cluster.read_token not in json.dumps(body)

    # The Sandbox is still there (shutdownTime is a minute away): look at what the controller did.
    sandbox, = cluster.sandboxes()
    name = sandbox['metadata']['name']
    spec = sandbox['spec']
    conditions = {c['type']: c for c in sandbox['status']['conditions']}
    print(f'[e2e] sandbox conditions: {json.dumps(conditions)}')
    # Open question 2: Finished / PodSucceeded, and the log came back through limitBytes.
    assert conditions['Finished']['status'] == 'True' and conditions['Finished']['reason'] == 'PodSucceeded'
    assert spec['shutdownPolicy'] == 'Delete' and spec['failedPodPolicy'] == 'Retain'
    shutdown = datetime.fromisoformat(spec['shutdownTime'].replace('Z', '+00:00'))
    created = datetime.fromisoformat(sandbox['metadata']['creationTimestamp'].replace('Z', '+00:00'))
    assert timedelta(seconds=60) < shutdown - created < timedelta(seconds=90)

    pod = cluster.kjson('-n', SANDBOX_NAMESPACE, 'get', 'pod', name)
    assert pod['status']['phase'] == 'Succeeded'
    pod_spec = pod['spec']
    assert pod_spec['automountServiceAccountToken'] is False
    assert pod_spec['securityContext']['runAsUser'] == 65532 and pod_spec['securityContext']['fsGroup'] == 65532
    assert not any('serviceaccount' in m['mountPath'] for m in pod_spec['containers'][0].get('volumeMounts', []))
    assert pod['metadata']['labels']['ai-jobs.hearthai/profile'] == 'memory-recall-v1'

    # Open question 1: the non-root worker read the 0400 Secret file (the envelope has the entry).
    secret = cluster.kjson('-n', SANDBOX_NAMESPACE, 'get', 'secret', name)
    owner, = secret['metadata']['ownerReferences']
    assert owner['kind'] == 'Sandbox' and owner['uid'] == sandbox['metadata']['uid']
    assert sorted(secret['data']) == ['credential', 'request.json']
    modes = {v['name']: v['secret'].get('defaultMode') for v in pod_spec['volumes'] if 'secret' in v}
    print(f'[e2e] per-run Secret volume defaultMode (decimal): {modes}')

    # Open question 3: all gone after shutdownTime, with no help from ai-jobs.
    elapsed = wait_until_gone(cluster, name)
    print(f'[e2e] sandbox, secret and pod gone {elapsed:.0f}s after the run returned')
    assert all(not names for names in cluster.objects_in_sandbox_namespace().values())


def test_extra_request_field_is_refused_before_anything_is_created(cluster, control_plane):
    before = cluster.objects_in_sandbox_namespace()
    status, body = post(control_plane, '/v1/tools/memory-recall',
                        {'query': 'recycling', 'limit': 1, 'image': 'evil:latest'}, cluster.caller_key)
    print(f'[e2e] extra field response: {status} {json.dumps(body)}')
    assert status == 400 and body['code'] == 'invalid_request'
    status, body = post(control_plane, '/v1/tools/memory-recall', {'query': 'x'}, None)
    assert status == 401
    status, body = post(control_plane, '/v1/tools/memory-recall', {'query': 'x'}, 'k' * 40)
    assert status == 401
    time.sleep(3)  # long enough for anything created asynchronously to show up
    assert cluster.objects_in_sandbox_namespace() == before == {
        'sandboxes.agents.x-k8s.io': [], 'secrets': [], 'pods': []}


def stale_sandbox(name, labelled=True, shutdown_in=3600):
    """A restricted-compliant Sandbox that just sleeps; labelled the way ai-jobs labels its own."""
    shutdown = (datetime.now(timezone.utc) + timedelta(seconds=shutdown_in)).strftime('%Y-%m-%dT%H:%M:%SZ')
    labels = {'app.kubernetes.io/managed-by': 'ai-jobs'} if labelled else {'e2e': 'foreign'}
    return {
        'apiVersion': 'agents.x-k8s.io/v1beta1', 'kind': 'Sandbox',
        'metadata': {'name': name, 'namespace': SANDBOX_NAMESPACE, 'labels': labels},
        'spec': {'service': False, 'shutdownTime': shutdown, 'shutdownPolicy': 'Delete', 'operatingMode': 'Running',
                 'podTemplate': {'metadata': {'labels': labels}, 'spec': {
                     'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                     'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532,
                                         'seccompProfile': {'type': 'RuntimeDefault'}},
                     'containers': [{'name': 'sleep', 'image': AI_JOBS_IMAGE, 'imagePullPolicy': 'IfNotPresent',
                                     'command': ['python3', '-c', 'import time; time.sleep(3600)'],
                                     'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'},
                                                   'limits': {'cpu': '50m', 'memory': '64Mi'}},
                                     'securityContext': {'allowPrivilegeEscalation': False,
                                                         'readOnlyRootFilesystem': True,
                                                         'capabilities': {'drop': ['ALL']}}}]}}},
    }


def test_startup_sweep_removes_only_stale_ai_jobs_sandboxes(cluster):
    """Open question 4. The sweep's cutoff is the longest deadline (15 s) plus the 60 s grace."""
    cluster.kubectl('apply', '-f', '-', input=json.dumps(stale_sandbox('e2e-stale')))
    cluster.kubectl('apply', '-f', '-', input=json.dumps(stale_sandbox('e2e-foreign', labelled=False)))
    time.sleep(80)
    cluster.kubectl('apply', '-f', '-', input=json.dumps(stale_sandbox('e2e-fresh')))
    cluster.kubectl('-n', NAMESPACE, 'rollout', 'restart', f'deploy/{RELEASE}-ai-jobs')
    cluster.kubectl('-n', NAMESPACE, 'rollout', 'status', f'deploy/{RELEASE}-ai-jobs', '--timeout=180s')

    def names():
        return sorted(s['metadata']['name'] for s in cluster.sandboxes())
    cluster.wait_for('the stale sandbox to be swept', lambda: 'e2e-stale' not in names(), 60, interval=2)
    print(f'[e2e] after the restart sweep: {names()}')
    assert names() == ['e2e-foreign', 'e2e-fresh']
    logs = cluster.kubectl('-n', NAMESPACE, 'logs', f'deploy/{RELEASE}-ai-jobs')
    assert 'Traceback' not in logs
    for name in ('e2e-foreign', 'e2e-fresh'):
        cluster.kubectl('-n', SANDBOX_NAMESPACE, 'delete', 'sandboxes.agents.x-k8s.io', name, '--wait=false')
