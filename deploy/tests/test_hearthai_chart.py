"""Deployment contracts, tested on rendered Kubernetes objects."""
import os
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'deploy/charts/hearthai'
EXAMPLE = ROOT / 'deploy/examples/hearthai-values.yaml'
HELM = os.environ.get('HELM', 'helm')


def render(*options, chart=CHART, example=True, success=True):
    args = [HELM, 'template', 'household', str(chart), '--namespace', 'ai']
    if example:
        args += ['-f', str(EXAMPLE)]
    proc = subprocess.run(args + list(options), capture_output=True, text=True)
    if not success:
        assert proc.returncode != 0, 'invalid inputs unexpectedly rendered'
        return proc.stderr
    assert proc.returncode == 0, proc.stderr
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def web(docs):
    return next(d for d in docs if d['kind'] == 'Deployment'
                and d['metadata']['labels'].get('app.kubernetes.io/component') == 'web')


def config(docs):
    return next(d for d in docs if d['kind'] == 'ConfigMap' and 'WEBUI_URL' in d['data'])['data']


def test_cohesive_release_and_oidc():
    docs = render()
    deployments = [d for d in docs if d['kind'] == 'Deployment']
    assert len(deployments) == 4
    for dep in deployments:
        assert dep['spec']['replicas'] == 1
        assert dep['spec']['strategy']['type'] == 'Recreate'
        selector = dep['spec']['selector']['matchLabels']
        assert selector.items() <= dep['spec']['template']['metadata']['labels'].items()
        services = [s for s in docs if s['kind'] == 'Service' and s['spec']['selector'] == selector]
        assert len(services) == 1
    assert deployments[0]['spec']['selector'] != deployments[1]['spec']['selector']
    dep = web(docs)
    pod = dep['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['volumes'][0]['persistentVolumeClaim']['claimName'] == 'open-webui-data'
    container = pod['containers'][0]
    env = {v['name']: v for v in container['env']}
    assert all('valueFrom' in v for v in env.values())
    assert env['WEBUI_SECRET_KEY']['valueFrom']['secretKeyRef']['name'] == 'open-webui-secret'
    assert 'OAUTH_CLIENT_ID' in env and 'WEBUI_ADMIN_PASSWORD' not in env
    assert not any(d['kind'] == 'Secret' for d in docs)
    cfg = config(docs)
    assert cfg['OPENID_REDIRECT_URI'] == 'https://chat.example.com/oauth/oidc/callback'
    assert cfg['ENABLE_LOGIN_FORM'] == 'false'
    assert cfg['ENABLE_PERSISTENT_CONFIG'] == 'false'
    assert cfg['ENABLE_CODE_EXECUTION'] == cfg['ENABLE_CODE_INTERPRETER'] == 'false'
    ingresses = [d for d in docs if d['kind'] == 'Ingress' and d['metadata']['name'] == dep['metadata']['name']]
    assert len(ingresses) == 1
    ingress = ingresses[0]['spec']
    assert ingress['rules'][0]['host'] == 'chat.example.com'
    assert ingress['tls'][0]['hosts'] == ['chat.example.com']
    assert ingress['rules'][0]['http']['paths'][0]['backend']['service']['name'] == dep['metadata']['name']


def test_local_bootstrap_without_oidc():
    docs = render('--set', 'auth.mode=local,auth.local.adminSecret.name=bootstrap,auth.oidc.discoveryUrl=,auth.oidc.credentialsSecret.name=')
    cfg = config(docs)
    assert cfg['ENABLE_LOGIN_FORM'] == 'true'
    assert cfg['ENABLE_SIGNUP'] == cfg['ENABLE_OAUTH_SIGNUP'] == 'false'
    assert 'OPENID_PROVIDER_URL' not in cfg
    env = {e['name']: e for e in web(docs)['spec']['template']['spec']['containers'][0]['env']}
    assert 'OAUTH_CLIENT_SECRET' not in env
    assert env['WEBUI_ADMIN_PASSWORD']['valueFrom']['secretKeyRef']['name'] == 'bootstrap'


def test_new_retained_claims():
    docs = render('--set', 'openwebui.persistence.existingClaim=,hearthmem.persistence.existingClaim=,litellm.persistence.existingClaim=,meridian.persistence.existingClaim=')
    claims = [d for d in docs if d['kind'] == 'PersistentVolumeClaim']
    assert len(claims) == 4
    for claim in claims:
        assert claim['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
        assert claim['spec']['accessModes'] == ['ReadWriteOnce']
    for dep in [d for d in docs if d['kind'] == 'Deployment']:
        name = next(v['persistentVolumeClaim']['claimName'] for v in dep['spec']['template']['spec']['volumes'] if 'persistentVolumeClaim' in v)
        assert name in {c['metadata']['name'] for c in claims}


def test_explicit_empty_storage_class():
    docs = render('--set', 'openwebui.persistence.existingClaim=', '--set-string', 'openwebui.persistence.storageClass=')
    claim = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
    assert claim['spec']['storageClassName'] == ''


def test_ephemeral_and_external_ingress():
    docs = render('--set', 'litellm.enabled=false,meridian.enabled=false,llm.baseUrl=http://external:4000/v1,openwebui.persistence.enabled=false,hearthmem.persistence.enabled=false,ingress.enabled=false')
    assert not any(d['kind'] in ['PersistentVolumeClaim', 'Ingress'] for d in docs)
    for dep in [d for d in docs if d['kind'] == 'Deployment']:
        assert dep['spec']['template']['spec']['volumes'][0]['emptyDir'] == {}


def test_configuration_rolls_pod():
    def checksum(docs):
        return web(docs)['spec']['template']['metadata']['annotations']['checksum/config']
    assert checksum(render()) != checksum(render('--set', 'litellm.enabled=false,meridian.enabled=false,llm.baseUrl=http://different:4000/v1'))


@pytest.mark.parametrize('setting', [
    'url=http://chat.example.com', 'url=https://chat.example.com/path',
    'sessionSecret.name=', 'litellm.enabled=false,meridian.enabled=false,llm.baseUrl=', 'llm.apiKeySecret.name=',
    'litellm.existingSecret=', 'litellm.ingress.enabled=true,litellm.ingress.host=',
    'auth.mode=anonymous', 'auth.oidc.discoveryUrl=',
    'auth.oidc.credentialsSecret.name=',
    'auth.mode=local,auth.local.adminSecret.name=',
])
def test_invalid_inputs(setting):
    render('--set', setting, success=False)


def test_missing_inputs():
    render(example=False, success=False)


def test_packaged_chart_is_self_contained(tmp_path):
    subprocess.run([HELM, 'package', str(CHART), '--destination', str(tmp_path)], check=True, capture_output=True)
    archive = next(tmp_path.glob('hearthai-*.tgz'))
    docs = render(chart=archive)
    assert len([d for d in docs if d['kind'] == 'Deployment']) == 4
    assert next(d for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])['data']['config.yaml'] == bundled_config()


def proxy(docs):
    return next(d for d in docs if d['kind'] == 'Deployment'
                and d['metadata']['labels']['app.kubernetes.io/name'] == 'litellm')


def test_exact_home_ops_catalogue():
    # Source: home-ops 6221eb6, app/configmap.yaml data.config.yaml.
    raw = (CHART / 'files/litellm-config.yaml').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'ad67fa2a12dd671629f9b10df69110a42484cc561232897d46c4cac59f4c0c09'
    docs = render()
    rendered = next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])
    assert rendered == bundled_config()
    models = yaml.safe_load(rendered)['model_list']
    assert [m['litellm_params']['model'] for m in models[:6]] == [
        'chatgpt/gpt-6-astra', 'chatgpt/gpt-5.6-sol', 'chatgpt/gpt-5.6-terra',
        'chatgpt/gpt-5.6-luna', 'chatgpt/gpt-5.5', 'chatgpt/gpt-5.4-mini',
    ]
    assert all(m['model_info']['mode'] == 'responses' for m in models[:6])


def test_proxy_runtime_and_internal_wiring():
    docs = render()
    dep = proxy(docs)
    pod = dep['spec']['template']['spec']
    assert pod['nodeSelector'] == {'kubernetes.io/arch': 'amd64'}
    init = pod['initContainers'][0]
    assert init['image'] == 'ghcr.io/home-operations/postgres-init:18.6'
    app = pod['containers'][0]
    assert app['image'] == 'ghcr.io/berriai/litellm-database:v1.99.1'
    assert app['args'] == ['--config', '/config/config.yaml', '--port', '4000']
    assert app['envFrom'] == init['envFrom'] == [{'secretRef': {'name': 'litellm-secret'}}]
    assert {e['name']: e['value'] for e in app['env']} == {'STORE_MODEL_IN_DB': 'True', 'CHATGPT_TOKEN_DIR': '/token'}
    assert app['readinessProbe']['httpGet']['path'] == '/health/readiness'
    assert app['livenessProbe']['httpGet']['path'] == '/health/liveliness'
    assert app['startupProbe']['failureThreshold'] == 60
    assert next(v for v in pod['volumes'] if v['name'] == 'token')['persistentVolumeClaim']['claimName'] == 'litellm-token'
    assert config(docs)['OPENAI_API_BASE_URLS'] == f"http://{dep['metadata']['name']}:4000/v1"
    # Imported virtual key stays the consumer credential; never use master key.
    env = web(docs)['spec']['template']['spec']['containers'][0]['env']
    assert next(e for e in env if e['name'] == 'OPENAI_API_KEYS')['valueFrom']['secretKeyRef'] == {'name': 'open-webui-secret', 'key': 'OPENAI_API_KEYS'}


def test_external_proxy_escape_hatch():
    docs = render('--set', 'litellm.enabled=false,meridian.enabled=false,llm.baseUrl=http://existing:4000/v1')
    assert len([d for d in docs if d['kind'] == 'Deployment']) == 2
    assert config(docs)['OPENAI_API_BASE_URLS'] == 'http://existing:4000/v1'
    assert not any('-litellm' in d['metadata']['name'] for d in docs)


def test_proxy_config_rollout_and_optional_database_init(tmp_path):
    def checksum(docs):
        return proxy(docs)['spec']['template']['metadata']['annotations']['checksum/config']
    # Operator can relocate the external Meridian endpoint through full config.
    original = (CHART / 'files/litellm-config.yaml').read_text()
    override = tmp_path / 'values.yaml'
    override.write_text(yaml.safe_dump({'litellm': {'config': original.replace('meridian.ai.svc.cluster.local', 'meridian.other.svc')}}))
    assert checksum(render()) != checksum(render('-f', str(override)))
    assert 'initContainers' not in proxy(render('--set', 'litellm.initDb.enabled=false'))['spec']['template']['spec']


def test_flux_example_matches_helm_values():
    source = list(yaml.safe_load_all((ROOT / 'deploy/examples/flux-hearthai.yaml').read_text()))
    assert source[1]['spec']['values'] == yaml.safe_load(EXAMPLE.read_text())


def bundled_config():
    return (CHART / 'files/litellm-config.yaml').read_text().replace(
        'http://meridian.ai.svc.cluster.local:3456',
        'http://household-hearthai-meridian:3456',
    )


def test_single_server_postgres_default_and_wiring():
    defaults = yaml.safe_load((CHART / 'values.yaml').read_text())
    assert defaults['postgres']['enabled'] is True
    docs = render('--set', 'postgres.enabled=true')
    db = next(d for d in docs if d['kind'] == 'Cluster')
    assert db['apiVersion'] == 'postgresql.cnpg.io/v1'
    assert db['spec']['instances'] == 1
    assert db['spec']['imageName'] == 'ghcr.io/cloudnative-pg/postgresql:16.0-10'
    assert db['spec']['bootstrap']['initdb'] == {'database': 'litellm', 'owner': 'litellm'}
    assert db['spec']['enableSuperuserAccess'] is False
    assert db['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
    assert 'plugins' not in db['spec']  # Never inherit shared backup history.
    assert 'monitoring' not in db['spec']  # No implicit PodMonitor requirement.
    pod = proxy(docs)['spec']['template']['spec']
    assert 'initContainers' not in pod
    env = pod['containers'][0]['env']
    assert next(v for v in env if v['name'] == 'DATABASE_URL')['valueFrom']['secretKeyRef'] == {
        'name': db['metadata']['name'] + '-app', 'key': 'uri',
    }
    assert not any(d['kind'] in ('ClusterRole', 'CustomResourceDefinition') for d in docs)


def test_external_postgres_uses_existing_secret():
    docs = render('--set', 'postgres.enabled=false')
    assert not any(d['kind'] == 'Cluster' for d in docs)
    pod = proxy(docs)['spec']['template']['spec']
    assert pod['initContainers'][0]['name'] == 'init-db'
    app = pod['containers'][0]
    assert not any(v['name'] == 'DATABASE_URL' for v in app['env'])
    assert app['envFrom'] == [{'secretRef': {'name': 'litellm-secret'}}]


def test_meridian_runtime_policy_and_internal_routing():
    docs = render()
    dep = next(d for d in docs if d['kind'] == 'Deployment'
               and d['metadata']['labels'].get('app.kubernetes.io/name') == 'meridian')
    pod = dep['spec']['template']['spec']
    app = pod['containers'][0]
    assert app['image'] == 'ghcr.io/rynfar/meridian:1.68.0'
    assert pod['securityContext']['runAsUser'] == 1000
    assert pod['automountServiceAccountToken'] is False
    assert app['securityContext']['capabilities']['drop'] == ['ALL']
    assert app['volumeMounts'][0]['mountPath'] == '/home/claude/.claude'
    assert pod['volumes'][0]['persistentVolumeClaim']['claimName'] == 'meridian-auth'
    key = next(v for v in app['env'] if v['name'] == 'MERIDIAN_API_KEY')
    assert key['valueFrom']['secretKeyRef'] == {'name': 'meridian-secret', 'key': 'MERIDIAN_API_KEY'}
    policy = next(d for d in docs if d['kind'] == 'CiliumNetworkPolicy')
    target = policy['spec']['endpointSelector']['matchLabels']
    assert target == dep['spec']['selector']['matchLabels']
    allowed = policy['spec']['ingress'][0]['fromEndpoints'][0]['matchLabels']
    assert allowed['app.kubernetes.io/instance'] == 'household'
    assert allowed['k8s:io.kubernetes.pod.namespace'] == 'ai'
    rendered = next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])
    claude = yaml.safe_load(rendered)['model_list'][6:]
    assert all(m['litellm_params']['api_base'] == 'http://' + dep['metadata']['name'] + ':3456' for m in claude)


def test_external_meridian_keeps_original_config():
    docs = render('--set', 'meridian.enabled=false')
    assert not any(d['kind'] == 'CiliumNetworkPolicy' for d in docs)
    rendered = next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])
    assert rendered == (CHART / 'files/litellm-config.yaml').read_text()


def test_custom_config_is_never_rewritten(tmp_path):
    raw = (CHART / 'files/litellm-config.yaml').read_text()
    values = tmp_path / 'override.yaml'
    values.write_text(yaml.safe_dump({'litellm': {'config': raw}}))
    docs = render('-f', str(values))
    assert next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data']) == raw


def test_meridian_secret_fallback_and_network_policy_option():
    docs = render('--set', 'meridian.existingSecret=,meridian.networkPolicy.enabled=false')
    dep = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['labels'].get('app.kubernetes.io/name') == 'meridian')
    env = dep['spec']['template']['spec']['containers'][0]['env']
    key = next(v for v in env if v['name'] == 'MERIDIAN_API_KEY')
    assert key['valueFrom']['secretKeyRef']['name'] == 'litellm-secret'
    assert not any(d['kind'] == 'CiliumNetworkPolicy' for d in docs)
