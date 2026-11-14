"""Docker harness for the lane test: closed network, real gate, recording stand-ins.

Nothing here is product code.  It only builds the environment the test runs in.
"""
import copy
import json
import os
import re
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

# Pinned to what home-ops runs; the chart default is older.  Override with
# HEARTHAI_LANE_LITELLM_IMAGE to rerun the same test against another build.
LITELLM_IMAGE = os.environ.get('HEARTHAI_LANE_LITELLM_IMAGE', 'ghcr.io/berriai/litellm-database:v1.106.6')
MASTER_KEY = 'sk-test-master'  # dummy; exists only inside the throwaway network
PREFIX = 'p1d-'


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


# The placeholder upstream never resolves; `.invalid` is reserved (RFC 6761).
LANE_OPTS = ['--set', 'localInference.enabled=true', '--set', 'localInference.upstreamUrl=http://laptop.invalid:11434']


def rendered_litellm_config_text(docs):
    return next(d['data']['config.yaml'] for d in docs
                if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])


def local_model_names(config):
    """Every name whose deployment is an `ollama_chat/` entry; never hardcoded."""
    return sorted({m['model_name'] for m in config['model_list']
                   if str(m['litellm_params']['model']).startswith('ollama_chat/')})


def deep_merge(base, extra):
    """Dicts merge, lists concatenate, anything else is replaced by `extra`."""
    if isinstance(base, dict) and isinstance(extra, dict):
        merged = dict(base)
        for key, value in extra.items():
            merged[key] = deep_merge(base[key], value) if key in base else copy.deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(extra, list):
        return base + copy.deepcopy(extra)
    return copy.deepcopy(extra)


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Stack:
    """One closed network with: LiteLLM, the real gate, three recording stand-ins, a client."""

    def __init__(self, scenario, workdir):
        self.scenario = scenario
        self.workdir = Path(workdir)
        self.run_id = uuid.uuid4().hex[:8]
        self.network = f'{PREFIX}lane-net-{self.run_id}'
        self.containers = []
        self.records = self.workdir / 'records'
        self.config_dir = self.workdir / 'config'
        self.token_dir = self.workdir / 'token'

        docs = helm_template(*LANE_OPTS, *scenario.helm_args)
        self.docs = docs
        base = yaml.safe_load(rendered_litellm_config_text(docs))
        self.chart_config = base
        self.local_names = scenario.model_names or local_model_names(base)
        service = next(d for d in docs if d['kind'] == 'Service' and d['metadata']['name'].endswith('-local'))
        self.gate_host = service['metadata']['name']
        self.gate_url = f"http://{self.gate_host}:{service['spec']['ports'][0]['port']}"
        self.cloud_url = self._single_base(m for m in base['model_list']
                                           if str(m['litellm_params']['model']).startswith('anthropic/'))
        self.cloud_host = urlparse(self.cloud_url).hostname
        # TEST HARNESS ONLY: the recorder callback is appended here, to a copy of the
        # rendered config.  The chart must never carry callbacks (the chart tests forbid it).
        harness = {'litellm_settings': {'callbacks': ['lane_recorder.recorder']}}
        self.config = deep_merge(deep_merge(base, scenario.extra_config), harness)

    @staticmethod
    def _single_base(entries):
        bases = {e['litellm_params']['api_base'] for e in entries}
        assert len(bases) == 1, bases
        return bases.pop()

    # -- lifecycle -------------------------------------------------------
    def _run(self, name, *docker_args, image, command=()):
        full = f'{PREFIX}lane-{name}-{self.run_id}'
        # A cold runner pulls the image here, which can take minutes.
        sh('docker', 'run', '-d', '--name', full, '--network', self.network, *docker_args, image, *command, timeout=1200)
        self.containers.append(full)
        return full

    def start(self):
        for path in (self.records, self.config_dir, self.token_dir):
            path.mkdir(parents=True)
        self.records.chmod(0o777)
        (self.config_dir / 'config.yaml').write_text(yaml.safe_dump(self.config, sort_keys=False))
        (self.config_dir / 'lane_recorder.py').write_text((HERE / 'lane_recorder.py').read_text())
        for name, content in self.scenario.extra_files.items():
            (self.config_dir / name).write_text(content)
        # A token that is valid for the rest of time, so a `chatgpt/` request would really
        # be attempted.  An expired one would refresh against auth.openai.com (blocked here)
        # and fail before any request, which would make "zero requests" prove nothing.
        (self.token_dir / 'auth.json').write_text(json.dumps(
            {'access_token': 'fake-access-token', 'expires_at': 4102444800, 'account_id': 'fake-account'}))

        sh('docker', 'network', 'create', '--internal', self.network)
        py = ['--entrypoint', 'python']
        mounts = ['-v', f'{HERE}:/harness:ro', '-v', f'{self.records}:/records']
        for label, port, alias in (('meridian', 3456, self.cloud_host), ('chatgpt', 8080, f'{PREFIX}chatgpt-standin')):
            self._run(label, '--network-alias', alias, *py, *mounts, image=LITELLM_IMAGE,
                      command=['/harness/standin.py', '--label', label, '--port', str(port), '--out', f'/records/{label}.jsonl'])

        # The real gate: the chart's own image, Caddyfile and environment, hardened as the chart does.
        gate_dep = next(d for d in self.docs if d['kind'] == 'Deployment' and d['metadata']['name'] == self.gate_host)
        gate_ct = gate_dep['spec']['template']['spec']['containers'][0]
        gate_cm = next(d for d in self.docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == self.gate_host)
        (self.workdir / 'gate').mkdir()
        (self.workdir / 'gate' / 'Caddyfile').write_text(gate_cm['data']['Caddyfile'])
        env = [x for e in gate_ct['env'] for x in ('-e', f"{e['name']}={e['value']}")]
        self._run('gate', '--network-alias', self.gate_host, '--user', '65532:65532', '--read-only',
                  '--cap-drop', 'ALL', '--tmpfs', '/data:uid=65532', '--tmpfs', '/config:uid=65532',
                  '-v', f'{self.workdir / "gate"}:/etc/caddy:ro', *env, image=gate_ct['image'], command=gate_ct['args'])

        self._run('litellm', '--network-alias', f'{PREFIX}litellm',
                  '-v', f'{self.config_dir}:/config:ro', '-v', f'{self.token_dir}:/token:ro', '-v', f'{self.records}:/records',
                  '-e', f'LITELLM_MASTER_KEY={MASTER_KEY}', '-e', 'MERIDIAN_API_KEY=sk-test-meridian',
                  '-e', 'CHATGPT_TOKEN_DIR=/token', '-e', f'CHATGPT_API_BASE=http://{PREFIX}chatgpt-standin:8080',
                  # Do not try to download the price map on a network with no way out.
                  '-e', 'LITELLM_LOCAL_MODEL_COST_MAP=True',
                  image=LITELLM_IMAGE, command=['--config', '/config/config.yaml', '--port', '4000'])
        self._run('client', *py, '-v', f'{HERE}:/harness:ro', image=LITELLM_IMAGE,
                  command=['-c', 'import time; time.sleep(86400)'])
        self.wait_ready()
        self.wait_quiet()
        return self

    def stop(self):
        for name in reversed(self.containers):
            sh('docker', 'rm', '-f', name, check=False)
        sh('docker', 'network', 'rm', self.network, check=False)

    def logs(self, name):
        return sh('docker', 'logs', f'{PREFIX}lane-{name}-{self.run_id}', check=False)

    # -- traffic ---------------------------------------------------------
    def http(self, method, path, body=None, headers=None, timeout=120):
        spec = {'url': f'http://{PREFIX}litellm:4000{path}', 'method': method, 'body': body,
                'headers': {'Authorization': f'Bearer {MASTER_KEY}', **(headers or {})}, 'timeout': timeout}
        out = sh('docker', 'exec', '-i', f'{PREFIX}lane-client-{self.run_id}', 'python', '/harness/client.py',
                 input=json.dumps(spec), timeout=timeout + 30)
        return json.loads(out.stdout)

    def wait_ready(self, timeout=240):
        deadline = time.time() + timeout
        while time.time() < deadline:
            running = sh('docker', 'inspect', '-f', '{{.State.Running}}', self.containers[-2], check=False).stdout.strip()
            if running != 'true':
                raise RuntimeError('litellm exited during startup:\n' + self.logs('litellm').stdout[-4000:])
            probe = sh('docker', 'exec', '-i', f'{PREFIX}lane-client-{self.run_id}', 'python', '/harness/client.py',
                       input=json.dumps({'url': f'http://{PREFIX}litellm:4000/v1/models', 'method': 'GET',
                                         'headers': {'Authorization': f'Bearer {MASTER_KEY}'}, 'timeout': 5}), check=False)
            if probe.returncode == 0:
                try:
                    reply = json.loads(probe.stdout)
                    if reply['status'] == 200:
                        return
                except ValueError:
                    pass
            time.sleep(2)
        raise RuntimeError('litellm not ready:\n' + self.logs('litellm').stdout[-4000:])

    def wait_quiet(self, quiet_for=10, timeout=120):
        """LiteLLM probes the cloud deployments itself at startup (`background_health_checks`, then
        every 300 s).  Those probes land on the stand-ins and are not requests for a local model.
        Wait until they have stopped, so the per-case windows start clean.  A later probe could in
        principle fall inside a window; the whole suite runs in about two minutes, well inside the
        interval, and a window that catches one fails loudly rather than passing."""
        deadline, last, since = time.time() + timeout, None, time.time()
        while time.time() < deadline:
            now = self.mark()
            if now != last:
                last, since = now, time.time()
            elif time.time() - since >= quiet_for:
                return
            time.sleep(1)
        raise RuntimeError(f'startup health probes never went quiet: {last}')

    def mark(self):
        """Offsets into every record file, so a case can read only what it caused."""
        return {name: len(read_jsonl(self.records / f'{name}.jsonl')) for name in ('meridian', 'chatgpt', 'callback')}

    def since(self, mark):
        return {name: read_jsonl(self.records / f'{name}.jsonl')[count:] for name, count in mark.items()}
