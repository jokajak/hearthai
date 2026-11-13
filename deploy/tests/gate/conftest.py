"""Harness for the gate behaviour tests: real Caddy image, scripted fake upstreams.

Everything lives on one user-defined docker network, so Caddy reaches each fake by its
network alias. Every container and the network are named `p1c-gate-*` (and labelled), and are
removed on teardown even if a test fails. Both the 4 s and the default rendering of the gate's
Caddyfile come from `helm template`, not from a copy.
"""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHART = ROOT / 'deploy/charts/hearthai'
EXAMPLE = ROOT / 'deploy/examples/hearthai-values.yaml'
HELM = os.environ.get('HELM', 'helm')
DOCKER = os.environ.get('DOCKER', 'docker')
FAKE_SCRIPT = HERE / 'fake_upstream.py'
CADDY_IMAGE = 'docker.io/library/caddy:2.11.9'
PYTHON_IMAGE = 'python:3.13-slim'
PREFIX = 'p1c-gate'
LABEL = 'p1c-gate=1'
RUN = uuid.uuid4().hex[:6]

GATE_BODY = ('{"error":{"type":"local_unavailable","message":"Local model unavailable: the laptop is off, '
             'asleep or off the network. This model never falls back to another provider."}}')


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: runs for minutes (the two-minute streaming case); CI runs it')


def pytest_collection_modifyitems(config, items):
    """`pytest deploy/tests` (the chart contract job) must not need docker: only collect
    the gate tests when the invocation names this directory."""
    named = any(str(HERE) in str((Path.cwd() / a.split('::')[0]).resolve()) for a in config.args)
    if named:
        return
    gate = [i for i in items if Path(str(i.fspath)).is_relative_to(HERE)]
    if gate:
        config.hook.pytest_deselected(items=gate)
        items[:] = [i for i in items if i not in gate]


def sh(*args, check=True, **kw):
    proc = subprocess.run(args, capture_output=True, text=True, **kw)
    if check and proc.returncode != 0:
        raise RuntimeError(f'{args} failed: {proc.stderr or proc.stdout}')
    return proc


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def render_caddyfile(*extra):
    out = sh(HELM, 'template', 'household', str(CHART), '--namespace', 'ai', '-f', str(EXAMPLE),
             '--set', 'localInference.enabled=true',
             '--set', 'localInference.upstreamUrl=http://placeholder.invalid:11434', *extra).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc['kind'] == 'ConfigMap' and 'Caddyfile' in doc.get('data', {}):
            return doc['data']['Caddyfile']
    raise AssertionError('no gate ConfigMap rendered')


def rendered_env_name():
    """The env var name the Deployment gives the gate, so the test cannot drift from it."""
    out = sh(HELM, 'template', 'household', str(CHART), '--namespace', 'ai', '-f', str(EXAMPLE),
             '--set', 'localInference.enabled=true',
             '--set', 'localInference.upstreamUrl=http://placeholder.invalid:11434').stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc['kind'] == 'Deployment' and doc['metadata']['name'].endswith('-local'):
            env = doc['spec']['template']['spec']['containers'][0]['env']
            assert len(env) == 1
            return env[0]['name']
    raise AssertionError('no gate Deployment rendered')


class Fake:
    def __init__(self, name, host_port, pid_of):
        self.name, self.host_port, self.pid_of = name, host_port, pid_of

    @property
    def pid(self):
        return int(sh(DOCKER, 'inspect', '-f', '{{.State.Pid}}', self.name).stdout)

    def stats(self):
        import http.client
        c = http.client.HTTPConnection('127.0.0.1', self.host_port, timeout=3)
        c.request('GET', '/_stats')
        return json.loads(c.getresponse().read())

    def blackhole(self, port=8080):
        """Drop (not reject) inbound SYNs to the fake, inside the fake's own network namespace."""
        sudo = [] if os.geteuid() == 0 else ['sudo', '-n']
        sh(*sudo, 'nsenter', '-t', str(self.pid), '-n',
           'iptables', '-I', 'INPUT', '-p', 'tcp', '--dport', str(port), '-j', 'DROP')


class Lab:
    def __init__(self):
        self.network = f'{PREFIX}-{RUN}-net'
        self.containers = []
        self.n = 0
        sh(DOCKER, 'network', 'create', '--label', LABEL, self.network)

    def _name(self, kind):
        self.n += 1
        return f'{PREFIX}-{RUN}-{kind}{self.n}'

    def _run(self, name, *args):
        self.containers.append(name)
        sh(DOCKER, 'run', '-d', '--name', name, '--label', LABEL, '--network', self.network,
           '--network-alias', name, *args)

    def fake(self, *fake_args, host_port=None, name=None):
        name = name or self._name('fake')
        host_port = host_port or free_port()
        self._run(name, '-p', f'127.0.0.1:{host_port}:8080',
                  '-v', f'{FAKE_SCRIPT}:/fake.py:ro', PYTHON_IMAGE, 'python', '-u', '/fake.py', *fake_args)
        fake = Fake(name, host_port, None)
        if '--no-listen' not in fake_args:
            self.wait_listening(host_port)
        return fake

    def restart_fake(self, fake):
        sh(DOCKER, 'start', fake.name)
        self.wait_listening(fake.host_port)

    def kill(self, fake):
        sh(DOCKER, 'kill', fake.name)

    def wait_listening(self, port, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                    pass
                # The published port accepts before the process inside does; ask it.
                import http.client
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                c.request('GET', '/_stats')
                if c.getresponse().status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(f'port {port} never came up')

    def gate(self, caddyfile, upstream, tmp_path):
        """Run the pinned Caddy image on the rendered Caddyfile; returns its host port."""
        cfg = tmp_path / f'caddy-{self._name("cfg")}'
        cfg.mkdir()
        # Mutation check: GATE_TEST_MUTATE='old=>new' edits the rendered copy (never the chart)
        # so a reviewer can watch a row fail, e.g. 'dial_timeout 3s=>dial_timeout 30s'.
        mutate = os.environ.get('GATE_TEST_MUTATE')
        if mutate:
            old, new = mutate.split('=>')
            assert old in caddyfile, old
            caddyfile = caddyfile.replace(old, new)
        (cfg / 'Caddyfile').write_text(caddyfile)
        port = free_port()
        name = self._name('gate')
        self._run(name, '-p', f'127.0.0.1:{port}:11434',
                  '-e', f'{rendered_env_name()}=http://{upstream.name}:8080',
                  '-v', f'{cfg}:/etc/caddy:ro', CADDY_IMAGE,
                  'caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        self.gate_name = name
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                import http.client
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                c.request('GET', '/_gate/healthz')
                if c.getresponse().read() == b'ok':
                    return port
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(sh(DOCKER, 'logs', name, check=False).stderr)

    def logs(self, name):
        p = sh(DOCKER, 'logs', name, check=False)
        return p.stdout + p.stderr

    def close(self):
        for name in reversed(self.containers):
            sh(DOCKER, 'rm', '-f', name, check=False)
        sh(DOCKER, 'network', 'rm', self.network, check=False)


@pytest.fixture(scope='session', autouse=True)
def docker_available():
    if sh(DOCKER, 'info', check=False).returncode != 0:
        pytest.fail('docker is required for the gate behaviour tests (a skip would hide the feature untested)')
    for image in (CADDY_IMAGE, PYTHON_IMAGE):
        if sh(DOCKER, 'image', 'inspect', image, check=False).returncode != 0:
            sh(DOCKER, 'pull', image)
    # Only our own leftovers from a crashed earlier run, found by label.
    ids = sh(DOCKER, 'ps', '-aq', '--filter', f'label={LABEL}').stdout.split()
    if ids:
        sh(DOCKER, 'rm', '-f', *ids)
    for net in sh(DOCKER, 'network', 'ls', '-q', '--filter', f'label={LABEL}').stdout.split():
        sh(DOCKER, 'network', 'rm', net, check=False)


@pytest.fixture
def lab():
    lab = Lab()
    try:
        yield lab
    finally:
        lab.close()


@pytest.fixture(scope='session')
def caddyfile_short():
    return render_caddyfile('--set', 'localInference.gate.responseHeaderTimeout=4s')


@pytest.fixture(scope='session')
def caddyfile_default():
    return render_caddyfile()
