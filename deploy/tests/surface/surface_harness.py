"""Docker harness for the Open WebUI surface test.  Test environment only; nothing here is product code."""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHART = ROOT / 'deploy/charts/hearthai'
EXAMPLE = ROOT / 'deploy/examples/hearthai-values.yaml'
HELM = os.environ.get('HELM', 'helm')
PREFIX = 'p1d-'

# Local-auth mode, so there is no identity provider to reach.  The adminSecret name is a
# placeholder: the chart reads WEBUI_ADMIN_* from that Secret, never from the ConfigMap.
LOCAL_AUTH = ['--set', 'auth.mode=local,auth.local.adminSecret.name=bootstrap,'
                       'auth.oidc.discoveryUrl=,auth.oidc.credentialsSecret.name=']
ADMIN_EMAIL = 'admin@example.invalid'
ADMIN_PASSWORD = 'throwaway-password-0'  # exists only in this throwaway container


def sh(*args, check=True, input=None, timeout=300):
    proc = subprocess.run(args, capture_output=True, text=True, input=input, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f'{" ".join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}')
    return proc


def helm_template(*options):
    args = [HELM, 'template', 'household', str(CHART), '--namespace', 'ai', '-f', str(EXAMPLE), *options]
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def owui_env(scenario, docs):
    """The environment the pod gets: the chart's ConfigMap, plus only the overrides below."""
    cm = next(d for d in docs if d['kind'] == 'ConfigMap' and 'WEBUI_URL' in d['data'])
    env = dict(cm['data'])
    # Supplied by Secrets in the real Deployment (deployment.yaml), so they are not in the ConfigMap.
    # Dummy values; the fake gateway ignores the key.
    env['WEBUI_SECRET_KEY'] = 'throwaway-session-secret'
    env['OPENAI_API_KEYS'] = 'sk-test-not-a-key'
    # Closed network: do not wait on update checks or model downloads that cannot succeed.
    env['OFFLINE_MODE'] = 'true'
    env['ENABLE_VERSION_UPDATE_CHECK'] = 'false'
    env.update(scenario.extra_env)
    return env


class Surface:
    def __init__(self, scenario, workdir):
        self.scenario, self.workdir = scenario, Path(workdir)
        self.run_id = uuid.uuid4().hex[:8]
        self.network = f'{PREFIX}surface-net-{self.run_id}'
        self.containers = []
        self.records = self.workdir / 'records'
        docs = helm_template(*LOCAL_AUTH, *scenario.helm_args)
        deployment = next(d for d in docs if d['kind'] == 'Deployment'
                          and d['metadata']['labels'].get('app.kubernetes.io/component') == 'web')
        self.image = deployment['spec']['template']['spec']['containers'][0]['image']
        self.env = owui_env(scenario, docs)
        # The gateway URL is the chart's own OPENAI_API_BASE_URLS; the fake takes that name on the
        # network, so the value is NOT overridden and the chart's wiring is what gets exercised.
        gateway = urlparse(self.env['OPENAI_API_BASE_URLS'])
        self.gateway_alias, self.gateway_port = gateway.hostname, gateway.port

    def _run(self, name, *docker_args, command=()):
        full = f'{PREFIX}surface-{name}-{self.run_id}'
        # A cold runner pulls the image here, which can take minutes.
        sh('docker', 'run', '-d', '--name', full, '--network', self.network, *docker_args, self.image, *command, timeout=1200)
        self.containers.append(full)
        return full

    def name(self, short):
        return f'{PREFIX}surface-{short}-{self.run_id}'

    def start(self):
        self.records.mkdir(parents=True)
        self.records.chmod(0o777)
        sh('docker', 'network', 'create', '--internal', self.network)
        # The fake and the client use Open WebUI's own image only for its Python; nothing else is pulled.
        mounts = ['-v', f'{HERE}:/harness:ro', '-v', f'{self.records}:/records']
        self._run('gateway', '--network-alias', self.gateway_alias, '--entrypoint', 'python', *mounts,
                  command=['/harness/fake_gateway.py', '--port', str(self.gateway_port), '--out', '/records/gateway.jsonl'])
        env = [x for k, v in self.env.items() for x in ('-e', f'{k}={v}')]
        self._run('web', '--network-alias', 'webui', *env)
        self.wait_ready()
        return self

    def stop(self):
        for name in reversed(self.containers):
            sh('docker', 'rm', '-f', name, check=False)
        sh('docker', 'network', 'rm', self.network, check=False)

    def logs(self):
        out = sh('docker', 'logs', self.name('web'), check=False)
        return out.stdout + out.stderr

    def http(self, method, path, body=None, token=None, timeout=60):
        spec = {'url': f'http://webui:8080{path}', 'method': method, 'body': body, 'timeout': timeout,
                'headers': {'Authorization': f'Bearer {token}'} if token else {}}
        proc = sh('docker', 'exec', '-i', self.name('gateway'), 'python', '/harness/owui_client.py',
                  input=json.dumps(spec), timeout=timeout + 30)
        reply = json.loads(proc.stdout)
        try:
            reply['json'] = json.loads(reply['body'])
        except ValueError:
            reply['json'] = None
        return reply

    def wait_ready(self, timeout=420):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if sh('docker', 'inspect', '-f', '{{.State.Running}}', self.name('web'), check=False).stdout.strip() != 'true':
                raise RuntimeError('open webui exited during startup:\n' + self.logs()[-4000:])
            probe = sh('docker', 'exec', '-i', self.name('gateway'), 'python', '/harness/owui_client.py', check=False,
                       input=json.dumps({'url': 'http://webui:8080/health', 'method': 'GET', 'timeout': 5}))
            if probe.returncode == 0 and json.loads(probe.stdout)['status'] == 200:
                return
            time.sleep(3)
        raise RuntimeError('open webui not ready:\n' + self.logs()[-4000:])

    def gateway_requests(self):
        return read_jsonl(self.records / 'gateway.jsonl')
