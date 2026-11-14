"""Harness for the ai-jobs kind end-to-end test (see test_ai_jobs_kind.py). Not a conftest.py on
purpose: deploy/tests has several, and a shared basename makes `from conftest import` ambiguous.

What this proves, and what it does not. A real API server runs the real control-plane image, the
real agent-sandbox v1.0.9 controller, the chart under test, and a real hearthmem built from this
tree. It proves lifecycle, admission and cleanup: a run becomes a Sandbox and a per-run Secret,
the pod is admitted under Pod Security `restricted`, its envelope comes back through pods/log, and
the objects disappear at shutdownTime. It does NOT prove isolation: kind's CNI (kindnet) enforces
no CiliumNetworkPolicy (the kind is only admitted, through a stub CRD) and kind has no gVisor.
Egress containment and the runtime class are checked on the real cluster by the owner.

Everything this creates is named after AI_JOBS_E2E_NAME (default `ai-jobs-e2e`): the kind cluster,
and the two image repositories. Teardown deletes the cluster; AI_JOBS_E2E_KEEP=1 leaves it.
AI_JOBS_E2E_SKIP_BUILD=1 reuses images already built under those names.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHART = ROOT / 'deploy/charts/hearthai'
NAME = os.environ.get('AI_JOBS_E2E_NAME', 'ai-jobs-e2e')
KIND = os.environ.get('KIND', 'kind')
KUBECTL = os.environ.get('KUBECTL', 'kubectl')
HELM = os.environ.get('HELM', 'helm')
DOCKER = os.environ.get('DOCKER', 'docker')

#: Pinned: the release asset's digest is checked, so a re-pointed tag cannot change this test.
AGENT_SANDBOX_VERSION = 'v1.0.9'
AGENT_SANDBOX_URL = (
    f'https://github.com/kubernetes-sigs/agent-sandbox/releases/download/{AGENT_SANDBOX_VERSION}/sandbox.yaml'
)
AGENT_SANDBOX_SHA256 = '3eeb27fa821659593ba92c5454571f08c84abb07096bb9d372c1c6c750f6d7d2'

RELEASE = 'hearthai'  # the home-ops Role binds the service account "hearthai-ai-jobs" in "ai"
NAMESPACE = 'ai'
SANDBOX_NAMESPACE = 'ai-jobs'
AI_JOBS_IMAGE = f'{NAME}/ai-jobs:e2e'
HEARTHMEM_IMAGE = f'{NAME}/hearthmem:e2e'

ENABLED = bool(os.environ.get('HEARTHAI_KIND_TESTS'))

KIND_CONFIG = """kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
"""
# For hosts where docker runs without CAP_SYS_RESOURCE on a cgroup v1 kernel, as in some nested
# sandboxes: runc then fails every pod with "failed to update /proc/self/oom_score_adj: Permission
# denied", and the systemd cgroup driver cannot make kubelet.slice under the v1 hugetlb controller.
# Not needed on GitHub's runners; set AI_JOBS_E2E_NESTED_HOST=1 where it is.
KIND_NESTED_HOST = """containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri"]
      restrict_oom_score_adj = true
    [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
      SystemdCgroup = false
kubeadmConfigPatches:
  - |
    kind: KubeletConfiguration
    cgroupDriver: cgroupfs
"""


class Failed(AssertionError):
    pass


class Cluster:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.kubeconfig = workdir / 'kubeconfig'
        self.env = dict(os.environ)
        # The API server is on a loopback port; a proxy configured for the runner must not see it.
        no_proxy = ','.join(filter(None, [os.environ.get('NO_PROXY', ''), '127.0.0.1', 'localhost']))
        self.env.update(KUBECONFIG=str(self.kubeconfig), NO_PROXY=no_proxy, no_proxy=no_proxy)
        self.caller_key = hashlib.sha256(os.urandom(32)).hexdigest()  # 64 characters
        self.read_token = ''
        self.forwards: list[subprocess.Popen] = []

    # ---- commands ---------------------------------------------------------------------------
    def run(self, *argv, check=True, timeout=300, input=None) -> subprocess.CompletedProcess:
        proc = subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=self.env,
                              timeout=timeout, input=input)
        if check and proc.returncode != 0:
            raise Failed(f'{" ".join(map(str, argv[:6]))} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}')
        return proc

    def kubectl(self, *args, **kw) -> str:
        return self.run(KUBECTL, *args, **kw).stdout

    def kjson(self, *args) -> dict:
        return json.loads(self.kubectl(*args, '-o', 'json'))

    def sandboxes(self) -> list[dict]:
        return self.kjson('get', 'sandboxes.agents.x-k8s.io', '-n', SANDBOX_NAMESPACE)['items']

    def objects_in_sandbox_namespace(self) -> dict[str, list[str]]:
        found = {}
        for kind in ('sandboxes.agents.x-k8s.io', 'secrets', 'pods'):
            items = self.kjson('get', kind, '-n', SANDBOX_NAMESPACE)['items']
            found[kind] = sorted(i['metadata']['name'] for i in items)
        return found

    def wait_for(self, what, predicate, timeout, interval=2):
        deadline = time.time() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.time() > deadline:
                raise Failed(f'timed out after {timeout}s waiting for {what}')
            time.sleep(interval)

    # ---- bring-up ---------------------------------------------------------------------------
    def log(self, message):
        print(f'[e2e] {message}', flush=True)

    def create_nested_host(self, create):
        """kind create, plus the one cgroup directory kubelet needs and kind does not make here.

        With AI_JOBS_E2E_NESTED_HOST the kubelet refuses to start because
        /sys/fs/cgroup/hugetlb/kubelet is missing; it is restarted by systemd, so creating the
        directory while `kind create` waits is enough.
        """
        proc = subprocess.Popen(create, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 600
        while proc.poll() is None and time.time() < deadline:
            subprocess.run([DOCKER, 'exec', f'{NAME}-control-plane', 'mkdir', '-p', '/sys/fs/cgroup/hugetlb/kubelet'],
                           capture_output=True)
            time.sleep(2)
        out = proc.communicate(timeout=30)[0] if proc.poll() is not None else ''
        if proc.returncode != 0:
            proc.kill()
            raise Failed(f'kind create cluster failed ({proc.returncode}):\n{out}')

    def load_image(self, image):
        loaded = self.run(KIND, 'load', 'docker-image', image, '--name', NAME, check=False, timeout=600)
        if loaded.returncode == 0:
            return
        # With docker's containerd image store a pulled multi-platform image is not fully present,
        # and kind's import wants every platform's content. Load just this host's platform.
        arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(self.run(DOCKER, 'info', '--format', '{{.Architecture}}').stdout.strip())
        archive = self.workdir / 'image.tar'
        self.run(DOCKER, 'save', '--platform', f'linux/{arch}', '-o', archive, image, timeout=600)
        self.run(KIND, 'load', 'image-archive', archive, '--name', NAME, timeout=600)
        archive.unlink()

    def quota_evaluated(self):
        """Wait until the quota that counts Sandboxes is evaluated, re-creating it if it is not.

        A quota that counts a custom resource refuses every create of it ("status unknown for
        quota") until the controller manager has discovered the CRD and recomputed the quota, and a
        quota created before discovery is not recomputed for minutes. On the real cluster the CRD is
        long since installed; here it was applied a moment ago, so the quota is re-created until it
        takes.
        """
        fixture = HERE / 'fixtures/home-ops-sandbox-namespace.yaml'
        for attempt in range(6):
            try:
                self.wait_for('the sandbox count quota to be evaluated',
                              lambda: 'count/sandboxes.agents.x-k8s.io' in self.kjson(
                                  '-n', SANDBOX_NAMESPACE, 'get', 'resourcequota', 'ai-jobs')['status'].get('used', {}),
                              40, interval=3)
                return
            except Failed:
                self.log('quota not evaluated yet; re-creating it')
                self.kubectl('-n', SANDBOX_NAMESPACE, 'delete', 'resourcequota', 'ai-jobs')
                self.kubectl('apply', '-f', fixture)
        raise Failed('the sandbox count quota was never evaluated')

    def build_images(self):
        if os.environ.get('AI_JOBS_E2E_SKIP_BUILD'):
            self.log('reusing prebuilt images')
            return
        self.log('building the ai-jobs image')
        self.run(DOCKER, 'build', '-t', AI_JOBS_IMAGE, str(ROOT / 'service/ai_jobs'), timeout=900)
        # The chart's hearthmem tag predates read-only tokens (A1), so the tree's own is used.
        self.log('building the hearthmem image from this tree')
        self.run(DOCKER, 'build', '-t', HEARTHMEM_IMAGE, str(ROOT / 'service'), timeout=900)

    def fetch_agent_sandbox(self) -> Path:
        path = self.workdir / 'sandbox.yaml'
        with urllib.request.urlopen(AGENT_SANDBOX_URL, timeout=120) as response:
            path.write_bytes(response.read())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == AGENT_SANDBOX_SHA256, f'sandbox.yaml {AGENT_SANDBOX_VERSION} changed: {digest}'
        return path

    def up(self):
        self.build_images()
        manifest = self.fetch_agent_sandbox()
        controller_images = re.findall(r'^\s*image:\s*([\w.-]+/[\w./-]+:v[\w.]+)\s*$', manifest.read_text(), re.M)
        assert len(controller_images) == 1 and controller_images[0].endswith(AGENT_SANDBOX_VERSION), controller_images

        self.log(f'creating kind cluster {NAME}')
        kind_config = self.workdir / 'kind.yaml'
        kind_config.write_text(KIND_CONFIG + (KIND_NESTED_HOST if os.environ.get('AI_JOBS_E2E_NESTED_HOST') else ''))
        create = [KIND, 'create', 'cluster', '--name', NAME, '--kubeconfig', str(self.kubeconfig),
                  '--config', str(kind_config), '--wait', '180s']
        if os.environ.get('AI_JOBS_E2E_NESTED_HOST'):
            self.create_nested_host(create)
        else:
            self.run(*create, timeout=600)
        # Images are pulled by the host's docker and loaded, never pulled by the node, so the run
        # does not depend on the node's registry access.
        self.run(DOCKER, 'pull', controller_images[0], timeout=600)
        for image in (AI_JOBS_IMAGE, HEARTHMEM_IMAGE, controller_images[0]):
            self.log(f'loading {image}')
            self.load_image(image)

        self.log('installing the CiliumNetworkPolicy stub CRD and agent-sandbox core')
        self.kubectl('apply', '-f', HERE / 'fixtures/cilium-crd-stub.yaml')
        self.kubectl('apply', '--server-side', '-f', manifest)
        for crd in ('sandboxes.agents.x-k8s.io', 'ciliumnetworkpolicies.cilium.io'):
            # `kubectl wait` errors out if the CRD has no status conditions yet, so poll.
            self.wait_for(f'CRD {crd} to be Established',
                          lambda crd=crd: any(c['type'] == 'Established' and c['status'] == 'True' for c in
                                              self.kjson('get', 'crd', crd)['status'].get('conditions') or []), 90)
        self.kubectl('-n', 'agent-sandbox-system', 'rollout', 'status', 'deploy/agent-sandbox-controller',
                     '--timeout=180s')

        self.log('applying the home-ops sandbox namespace objects (fixture)')
        self.kubectl('apply', '-f', HERE / 'fixtures/home-ops-sandbox-namespace.yaml')
        self.quota_evaluated()

        self.log('installing the chart with aiJobs disabled, to bring hearthmem up first')
        self.helm_release()
        self.kubectl('-n', NAMESPACE, 'rollout', 'status', f'deploy/{RELEASE}-hearthmem', '--timeout=180s')

        self.seed_hearthmem()
        self.kubectl('-n', NAMESPACE, 'create', 'secret', 'generic', 'ai-jobs-secret',
                     f'--from-literal=caller-keys.json={json.dumps({self.caller_key: "e2e-client"})}',
                     f'--from-literal=memory_recall_read_token={self.read_token}',
                     # Present in home-ops' Secret for Open WebUI's wiring; ai-jobs must not mount it.
                     f'--from-literal=caller_key_open_webui={self.caller_key}')

        self.log('enabling aiJobs with the memory-recall profile (the home-ops objects are already live)')
        self.helm_release('--set', 'aiJobs.enabled=true,aiJobs.existingSecret=ai-jobs-secret,'
                                   'aiJobs.profiles.memoryRecall.enabled=true')
        self.kubectl('-n', NAMESPACE, 'rollout', 'status', f'deploy/{RELEASE}-ai-jobs', '--timeout=180s')

    def helm_release(self, *extra):
        self.run(HELM, 'dependency', 'build', CHART)
        self.run(HELM, 'upgrade', '--install', RELEASE, CHART, '--namespace', NAMESPACE,
                 '-f', HERE / 'fixtures/values.yaml',
                 '--set', f'hearthmem.image.repository={NAME}/hearthmem,hearthmem.image.tag=e2e',
                 '--set', f'aiJobs.image.repository={NAME}/ai-jobs,aiJobs.image.tag=e2e',
                 *extra, timeout=300)

    def seed_hearthmem(self):
        """One store with two entries and a read token, made through hearthmem's own HTTP API."""
        script = '''
import json, urllib.request
def call(method, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Store-Token"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("http://127.0.0.1:8765" + path, method=method, data=data, headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=10))
write = call("POST", "/stores", {"purpose": "e2e household notes", "author": "e2e"})["token"]
for content in ("The spare key is under the blue flowerpot.", "Recycling goes out on Thursday."):
    call("POST", "/stores/-/entries", {"content": content, "author": "e2e", "tags": ["home"]}, token=write)
print(call("POST", "/stores/-/read-token", {}, token=write)["read_token"])
'''
        self.log('seeding hearthmem')
        out = self.kubectl('-n', NAMESPACE, 'exec', f'deploy/{RELEASE}-hearthmem', '--', 'python3', '-c', script)
        self.read_token = out.strip().splitlines()[-1]
        assert re.fullmatch(r'[A-Za-z0-9_-]{16,256}', self.read_token)

    # ---- reaching the control plane ----------------------------------------------------------
    def port_forward(self, target, remote_port) -> int:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            local = s.getsockname()[1]
        proc = subprocess.Popen([KUBECTL, '-n', NAMESPACE, 'port-forward', target, f'{local}:{remote_port}'],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.forwards.append(proc)

        def listening():
            try:
                socket.create_connection(('127.0.0.1', local), timeout=1).close()
                return True
            except OSError:
                return False

        self.wait_for(f'port-forward to {target}', listening, 30, interval=0.5)
        return local

    def diagnostics(self):
        if not self.kubeconfig.exists():
            return
        for command in (
            ['get', 'pods,sandboxes.agents.x-k8s.io,secrets', '-A', '-o', 'wide'],
            ['-n', SANDBOX_NAMESPACE, 'get', 'events', '--sort-by=.lastTimestamp'],
            ['-n', NAMESPACE, 'logs', f'deploy/{RELEASE}-ai-jobs', '--tail=80'],
            ['-n', 'agent-sandbox-system', 'logs', 'deploy/agent-sandbox-controller', '--tail=60'],
        ):
            proc = self.run(KUBECTL, *command, check=False, timeout=60)
            print(f'--- kubectl {" ".join(command)}\n{proc.stdout}{proc.stderr}', flush=True)

    def down(self):
        for proc in self.forwards:
            proc.terminate()
        if os.environ.get('AI_JOBS_E2E_KEEP'):
            self.log(f'leaving kind cluster {NAME} in place')
            return
        self.run(KIND, 'delete', 'cluster', '--name', NAME, check=False, timeout=300)
