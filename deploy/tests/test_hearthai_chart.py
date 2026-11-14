"""Deployment contracts, tested on rendered Kubernetes objects."""
import json
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


# ---------------------------------------------------------------------------
# Local lane: names served only by a machine on the owner's network.
# ---------------------------------------------------------------------------

# Config keys that can make a request for one model name be served by another
# deployment (LiteLLM v1.106.6 source). Add a key here and every render below
# is checked. Each entry is (section, key); section None means top level.
# `litellm_settings.callbacks` is listed because a callback can rewrite the
# model. A later package that adds a callback must remove it from this list
# deliberately, in the same change, and say why.
REROUTING_KEYS = [
    *[('router_settings', k) for k in (
        'fallbacks', 'context_window_fallbacks', 'content_policy_fallbacks',
        'default_fallbacks', 'model_group_alias', 'routing_groups', 'plugins')],
    *[('litellm_settings', k) for k in (
        'fallbacks', 'context_window_fallbacks', 'content_policy_fallbacks',
        'default_fallbacks', 'model_fallbacks', 'model_alias_map',
        'use_litellm_proxy', 'callbacks')],
    *[('general_settings', k) for k in ('completion_model', 'pass_through_endpoints')],
]
# Per model_list entry: litellm_params.model prefixes, litellm_params key
# prefixes, litellm_params keys, and model_info keys that reroute.
REROUTING_MODEL_PREFIXES = ['auto_router/', 'litellm_proxy/']
REROUTING_PARAM_PREFIXES = ['auto_router_', 'complexity_router_', 'adaptive_router_', 'quality_router_']
REROUTING_PARAM_KEYS = ['use_litellm_proxy']
REROUTING_INFO_KEYS = ['team_public_model_name']

LOCAL_OPTS = ['--set', 'localInference.enabled=true', '--set', 'localInference.upstreamUrl=laptop.invalid']


def litellm_config(docs):
    raw = next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])
    return yaml.safe_load(raw)


def helm_text(chart, *options, example=True):
    args = [HELM, 'template', 'household', str(chart), '--namespace', 'ai']
    if example:
        args += ['-f', str(EXAMPLE)]
    proc = subprocess.run(args + list(options), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def litellm_config_text(docs):
    return next(d['data']['config.yaml'] for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d['data'])


def test_disabled_lane_leaves_bundled_config_untouched():
    # The data value is the string home-ops deploys; compare text, not parsed YAML.
    unset = render()
    explicit = render('--set', 'localInference.enabled=false')
    assert litellm_config_text(unset) == bundled_config()
    assert litellm_config_text(explicit) == bundled_config()
    assert unset == explicit


def test_local_lane_requires_upstream_url():
    err = render('--set', 'localInference.enabled=true', success=False)
    assert 'localInference.upstreamUrl' in err


def test_local_names_appear_once_and_point_at_gate_service():
    docs = render(*LOCAL_OPTS)
    models = litellm_config(docs)['model_list']
    names = [m['model_name'] for m in models]
    local = [m for m in models if m['litellm_params']['model'].startswith('ollama_chat/')]
    assert [m['model_name'] for m in local] == ['local-qwen']
    for m in local:
        assert names.count(m['model_name']) == 1
        assert m['litellm_params']['api_base'] == 'http://household-hearthai-local:11434'
        assert m['litellm_params']['model'] == 'ollama_chat/qwen3.6:35b-a3b'
        assert m['model_info'] == {'mode': 'chat', 'max_input_tokens': 32768,
                                   'supports_function_calling': True,
                                   'disable_background_health_check': True}
    # Pre-existing catalogue is untouched and still first.
    assert len(models) == 10 and names[:9] == [m['model_name'] for m in litellm_config(render())['model_list']]


def test_duplicate_local_names_fail_to_render():
    # (a) collision with a cloud name
    err = render(*LOCAL_OPTS, '--set',
                 'localInference.models[0].name=gpt-5.5,localInference.models[0].ollamaModel=x,'
                 'localInference.models[0].contextWindow=1', success=False)
    assert 'gpt-5.5' in err and 'already used' in err
    # (b) two local entries with the same name
    values = ROOT / 'deploy/tests/.dup-values.tmp.yaml'
    entry = {'name': 'local-qwen', 'ollamaModel': 'x', 'contextWindow': 1}
    try:
        values.write_text(yaml.safe_dump({'localInference': {'models': [entry, dict(entry)]}}))
        err = render(*LOCAL_OPTS, '-f', str(values), success=False)
    finally:
        values.unlink(missing_ok=True)
    assert 'local-qwen' in err and 'already used' in err


def rerouting_violations(cfg):
    found = []
    for section, key in REROUTING_KEYS:
        if key in (cfg.get(section) or {}):
            found.append('%s.%s' % (section, key))
    for m in cfg.get('model_list') or []:
        name = str(m.get('model_name'))
        params = m.get('litellm_params') or {}
        info = m.get('model_info') or {}
        if '*' in name:
            found.append('wildcard model_name %s' % name)
        if any(str(params.get('model', '')).startswith(p) for p in REROUTING_MODEL_PREFIXES):
            found.append('%s: rerouting provider %s' % (name, params['model']))
        found += ['%s: litellm_params.%s' % (name, k) for k in params
                  if k in REROUTING_PARAM_KEYS or any(k.startswith(p) for p in REROUTING_PARAM_PREFIXES)]
        found += ['%s: model_info.%s' % (name, k) for k in info if k in REROUTING_INFO_KEYS]
    names = [m.get('model_name') for m in cfg.get('model_list') or []]
    found += ['duplicate model_name %s' % n for n in sorted(set(names)) if names.count(n) > 1]
    return found


@pytest.mark.parametrize('opts', [[], LOCAL_OPTS], ids=['disabled', 'enabled'])
def test_no_rerouting_keys_in_rendered_config(opts):
    assert rerouting_violations(litellm_config(render(*opts))) == []


def test_rerouting_check_detects_violations():
    cfg = litellm_config(render(*LOCAL_OPTS))
    cfg['router_settings'] = {'fallbacks': [{'local-qwen': ['gpt-5.5']}]}
    assert rerouting_violations(cfg) == ['router_settings.fallbacks']


@pytest.mark.parametrize('opts', [[], LOCAL_OPTS], ids=['disabled', 'enabled'])
def test_only_meridian_api_key_is_referenced_by_models(opts):
    for m in litellm_config(render(*opts))['model_list']:
        for key, value in (m['litellm_params'] or {}).items():
            if key == 'api_key' or str(value).startswith('os.environ/'):
                assert value == 'os.environ/MERIDIAN_API_KEY', (m['model_name'], key, value)


@pytest.mark.parametrize('opts', [[], LOCAL_OPTS], ids=['disabled', 'enabled'])
def test_task_models_unset_in_open_webui(opts):
    # Names confirmed at Open WebUI v0.11.3; both default to empty.
    data = config(render(*opts))
    assert not data.get('TASK_MODEL')
    assert not data.get('TASK_MODEL_EXTERNAL')


@pytest.mark.parametrize('opts', [[], LOCAL_OPTS], ids=['disabled', 'enabled'])
def test_litellm_args_have_no_model_override(opts):
    args = proxy(render(*opts))['spec']['template']['spec']['containers'][0]['args']
    assert not [a for a in args if a == '--model' or a.startswith('--model=')]


# ---------------------------------------------------------------------------
# Local gate: Caddy in front of the local host.
# ---------------------------------------------------------------------------

GATE_URL = 'http://laptop.example.internal:11434'
GATE_OPTS = ['--set', 'localInference.enabled=true', '--set', f'localInference.upstreamUrl={GATE_URL}']
GATE_BODY = ('{"error":{"type":"local_unavailable","message":"Local model unavailable: the laptop is off, '
             'asleep or off the network. This model never falls back to another provider."}}')
GATE_NAME = 'household-hearthai-local'


def gate_objects(docs):
    return [d for d in docs if d['metadata']['name'] == GATE_NAME]


def gate_of(docs, kind):
    found = [d for d in gate_objects(docs) if d['kind'] == kind]
    assert len(found) == 1
    return found[0]


def test_gate_absent_by_default():
    docs = render()
    assert gate_objects(docs) == []
    assert not any(d['kind'] == 'CiliumNetworkPolicy' and d['metadata']['name'].endswith('-local') for d in docs)


def test_gate_renders_one_of_each_when_enabled():
    docs = render(*GATE_OPTS)
    assert sorted(d['kind'] for d in gate_objects(docs)) == ['CiliumNetworkPolicy', 'ConfigMap', 'Deployment', 'Service']
    # The LiteLLM entry points at the Service this template creates.
    cfg = litellm_config(docs)
    local = [m for m in cfg['model_list'] if m['model_name'] == 'local-qwen']
    assert local[0]['litellm_params']['api_base'] == f'http://{GATE_NAME}:11434'


def test_gate_upstream_url_only_in_deployment_env():
    docs = render(*GATE_OPTS)
    for d in docs:
        text = yaml.safe_dump(d)
        if d['kind'] == 'Deployment' and d['metadata']['name'] == GATE_NAME:
            env = d['spec']['template']['spec']['containers'][0]['env']
            assert [e for e in env if e['value'] == GATE_URL] == [e for e in env if e['name'] == 'LOCAL_UPSTREAM_URL']
            stripped = yaml.safe_dump({k: v for k, v in d.items() if k != 'spec'})
            assert 'laptop.example.internal' not in stripped
            assert 'laptop.example.internal' not in yaml.safe_dump(d['spec']['template']['metadata'])
            assert text.count('laptop.example.internal') == 1
        else:
            assert 'laptop.example.internal' not in text, d['kind'] + '/' + d['metadata']['name']


def test_gate_pod_has_no_secrets_or_token():
    pod = gate_of(render(*GATE_OPTS), 'Deployment')['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert not any('secret' in v for v in pod.get('volumes', []))
    assert not any('projected' in v for v in pod.get('volumes', []))
    container = pod['containers'][0]
    assert 'envFrom' not in container
    assert not any('valueFrom' in e for e in container['env'])
    assert pod['securityContext']['runAsNonRoot'] is True
    assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
    sc = container['securityContext']
    assert sc['allowPrivilegeEscalation'] is False
    assert sc['readOnlyRootFilesystem'] is True
    assert sc['capabilities']['drop'] == ['ALL']


def test_gate_service_selects_gate_pods_on_11434():
    docs = render(*GATE_OPTS)
    dep = gate_of(docs, 'Deployment')
    svc = gate_of(docs, 'Service')
    assert svc['spec']['selector'] == dep['spec']['selector']['matchLabels']
    assert svc['spec']['selector'].items() <= dep['spec']['template']['metadata']['labels'].items()
    port = svc['spec']['ports'][0]
    assert port['port'] == 11434
    container_ports = {p['name']: p['containerPort'] for p in dep['spec']['template']['spec']['containers'][0]['ports']}
    assert container_ports[port['targetPort']] == 11434
    # No other workload's Service selects the gate pods.
    assert [s for s in docs if s['kind'] == 'Service' and s['spec']['selector'] == svc['spec']['selector']] == [svc]


def test_gate_probes_do_not_depend_on_the_laptop():
    container = gate_of(render(*GATE_OPTS), 'Deployment')['spec']['template']['spec']['containers'][0]
    for probe in ('livenessProbe', 'readinessProbe'):
        assert container[probe]['httpGet']['path'] == '/_gate/healthz'
    caddyfile = gate_of(render(*GATE_OPTS), 'ConfigMap')['data']['Caddyfile']
    assert 'handle /_gate/healthz' in caddyfile
    assert caddyfile.index('handle /_gate/healthz') < caddyfile.index('reverse_proxy')


def policy_sources(docs):
    policy = gate_of(docs, 'CiliumNetworkPolicy')
    assert len(policy['spec']['ingress']) == 2
    for rule in policy['spec']['ingress']:
        assert rule['toPorts'] == [{'ports': [{'port': '11434', 'protocol': 'TCP'}]}]
    assert policy['spec']['ingress'][1]['fromEntities'] == ['host', 'remote-node']
    return policy['spec']['ingress'][0]['fromEndpoints']


def test_gate_policy_admits_only_litellm_by_default():
    docs = render(*GATE_OPTS)
    sources = policy_sources(docs)
    assert sources == [{'matchLabels': {
        'app.kubernetes.io/name': 'litellm',
        'app.kubernetes.io/instance': 'household',
        'k8s:io.kubernetes.pod.namespace': 'ai',
    }}]
    policy = gate_of(docs, 'CiliumNetworkPolicy')
    dep = gate_of(docs, 'Deployment')
    assert policy['spec']['endpointSelector']['matchLabels'] == dep['spec']['selector']['matchLabels']


def test_gate_policy_additional_ingress_adds_exactly_one_scoped_selector():
    docs = render(*GATE_OPTS, '--set-json',
                  'localInference.gate.networkPolicy.additionalIngress=[{"namespace":"monitoring","matchLabels":{"app.kubernetes.io/name":"prober"}}]')
    sources = policy_sources(docs)
    assert len(sources) == 2
    assert sources[1] == {'matchLabels': {
        'app.kubernetes.io/name': 'prober',
        'k8s:io.kubernetes.pod.namespace': 'monitoring',
    }}


def test_gate_policy_can_be_disabled():
    docs = render(*GATE_OPTS, '--set', 'localInference.gate.networkPolicy.enabled=false')
    assert sorted(d['kind'] for d in gate_objects(docs)) == ['ConfigMap', 'Deployment', 'Service']
    assert not any(d['kind'] == 'CiliumNetworkPolicy' and d['metadata']['name'] == GATE_NAME for d in docs)


def caddy_error_body(caddyfile):
    start = caddyfile.index('respond `') + len('respond `')
    end = caddyfile.index('` 503', start)
    return caddyfile[start:end]


def test_gate_config_contains_the_contract():
    caddyfile = gate_of(render(*GATE_OPTS), 'ConfigMap')['data']['Caddyfile']
    assert json.loads(caddy_error_body(caddyfile)) == json.loads(GATE_BODY)
    assert caddy_error_body(caddyfile) == GATE_BODY
    lines = [line.strip() for line in caddyfile.splitlines()]
    for expected in ('dial_timeout 3s', 'response_header_timeout 180s', 'health_uri /api/version',
                     'health_interval 10s', 'health_timeout 2s', 'flush_interval -1',
                     'header Content-Type application/json', 'admin off', 'auto_https off',
                     'reverse_proxy {$LOCAL_UPSTREAM_URL} {', ':11434 {'):
        assert expected in lines, expected
    assert 'handle_errors {' in lines


def test_gate_response_header_timeout_comes_from_values():
    caddyfile = gate_of(render(*GATE_OPTS, '--set', 'localInference.gate.responseHeaderTimeout=240s'),
                        'ConfigMap')['data']['Caddyfile']
    assert 'response_header_timeout 240s' in caddyfile
    assert '180s' not in caddyfile
    render(*GATE_OPTS, '--set', 'localInference.gate.responseHeaderTimeout=soon', success=False)


def test_gate_config_change_rolls_the_pod():
    def checksum(*opts):
        return gate_of(render(*GATE_OPTS, *opts), 'Deployment')['spec']['template']['metadata']['annotations']['checksum/config']
    assert checksum() != checksum('--set', 'localInference.gate.responseHeaderTimeout=240s')
    assert checksum() == checksum()


def test_gate_image_is_fully_qualified_for_renovate():
    image = gate_of(render(*GATE_OPTS), 'Deployment')['spec']['template']['spec']['containers'][0]['image']
    assert image == yaml.safe_load((CHART / 'values.yaml').read_text())['localInference']['gate']['image']
    assert image.startswith('docker.io/library/caddy:')
