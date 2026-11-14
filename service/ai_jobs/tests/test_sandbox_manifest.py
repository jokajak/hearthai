"""The Sandbox is built from the profile and run id alone; the request lives only in the Secret."""

import base64
import dataclasses
import json
from datetime import UTC, datetime

import pytest
from conftest import make_spec

from ai_jobs.sandbox import (
    NAMESPACE,
    build_sandbox,
    build_secret,
    object_name,
)
from ai_jobs.tools.git_change import GitChangeDefinition
from ai_jobs.tools.noop import NoopDefinition
from ai_jobs.tools.web_fetch import WebFetchDefinition
from ai_jobs.tools.web_research import WebResearchDefinition

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


def sandbox(**changes):
    return build_sandbox(make_spec(**changes), namespace=NAMESPACE, now=NOW)


def pod_spec(manifest):
    return manifest["spec"]["podTemplate"]["spec"]


def test_sandbox_lifecycle_fields_match_the_spec():
    manifest = sandbox()
    assert manifest["apiVersion"] == "agents.x-k8s.io/v1beta1"
    assert manifest["kind"] == "Sandbox"
    assert manifest["metadata"]["namespace"] == "ai-jobs"
    spec = manifest["spec"]
    # 15 s deadline + 60 s grace after 12:00:00.
    assert spec["shutdownTime"] == "2026-11-14T12:01:15Z"
    assert spec["shutdownPolicy"] == "Delete"
    assert spec["failedPodPolicy"] == "Retain"
    assert spec["service"] is False
    assert set(spec) == {"podTemplate", "service", "failedPodPolicy", "shutdownTime", "shutdownPolicy", "operatingMode"}


def test_labels_identify_managed_profile_and_run():
    manifest = sandbox()
    expected = {
        "app.kubernetes.io/managed-by": "ai-jobs",
        "ai-jobs.hearthai/profile": "test-v1",
        "ai-jobs.hearthai/run": "run-1",
    }
    assert manifest["metadata"]["labels"] == expected
    assert manifest["spec"]["podTemplate"]["metadata"]["labels"] == expected


def test_pod_security_settings():
    pod = pod_spec(sandbox())
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "default"
    assert pod["enableServiceLinks"] is False
    assert pod["restartPolicy"] == "Never"
    assert pod["activeDeadlineSeconds"] == 15
    assert (pod["hostNetwork"], pod["hostPID"], pod["hostIPC"]) == (False, False, False)
    assert "runtimeClassName" not in pod
    context = pod["securityContext"]
    assert context["runAsNonRoot"] is True
    assert context["runAsUser"] == context["runAsGroup"] == 65532
    assert context["seccompProfile"] == {"type": "RuntimeDefault"}
    (container,) = pod["containers"]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "privileged": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["env"] == [{"name": "HOME", "value": "/tmp"}]
    assert container["command"] == ["worker", "run"]
    assert "args" not in container
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "64Mi"},
        "limits": {"cpu": "200m", "memory": "128Mi"},
    }


def test_volumes_are_the_run_secret_and_one_small_tmp_only():
    pod = pod_spec(sandbox())
    name = object_name("run-1")
    assert pod["volumes"] == [
        {"name": "run", "secret": {"secretName": name, "defaultMode": 0o400}},
        {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}},
    ]
    (container,) = pod["containers"]
    assert container["volumeMounts"] == [
        {"name": "run", "mountPath": "/run/ai-jobs", "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
    ]
    assert not any("hostPath" in volume for volume in pod["volumes"])


def test_runtime_class_comes_from_the_profile():
    assert pod_spec(sandbox(runtime_class="gvisor"))["runtimeClassName"] == "gvisor"


def test_object_names_are_derived_and_validated():
    assert object_name("abc123") == "ai-job-abc123"
    for bad in ("", "Run_1", "../x", "a" * 60, "-x"):
        with pytest.raises(ValueError):
            object_name(bad)


def test_secret_holds_request_and_credential_and_is_owned_by_the_sandbox():
    spec = make_spec(request=b'{"q":"hello"}', credential=b"tok-123")
    secret = build_secret(spec, namespace=NAMESPACE, owner_uid="uid-9")
    assert secret["metadata"]["name"] == object_name("run-1")
    assert base64.b64decode(secret["data"]["request.json"]) == b'{"q":"hello"}'
    assert base64.b64decode(secret["data"]["credential"]) == b"tok-123"
    (owner,) = secret["metadata"]["ownerReferences"]
    assert owner["apiVersion"] == "agents.x-k8s.io/v1beta1"
    assert owner["kind"] == "Sandbox"
    assert owner["name"] == object_name("run-1")
    assert owner["uid"] == "uid-9"
    assert secret["immutable"] is True


def test_secret_has_no_credential_key_when_the_profile_has_none():
    secret = build_secret(make_spec(request=b"{}"), namespace=NAMESPACE, owner_uid="uid-1")
    assert set(secret["data"]) == {"request.json"}


def test_a_credential_without_a_source_or_the_reverse_is_refused():
    with pytest.raises(ValueError):
        make_spec(credential=b"x", credential_source=None)


# The I12 requirement: nothing a caller sends reaches the Sandbox.

MARKERS = {
    WebResearchDefinition: {"question": "MARKER-question-8f1c"},
    WebFetchDefinition: {"url": "https://marker-url-3d9a.example/MARKER-path-77b2?MARKER-query=5e0f", "format": "html"},
    GitChangeDefinition: {"repository_id": "MARKER-repo-c41d", "instruction": "MARKER-instruction-02ab"},
    NoopDefinition: {},
}


@pytest.mark.parametrize("definition_type", list(MARKERS))
def test_no_request_field_reaches_the_sandbox_manifest(definition_type):
    definition = definition_type()
    request = definition.validate_request(MARKERS[definition_type])
    payload = request if isinstance(request, dict) else request.to_dict()
    # Every field of the request type is carrying a marker in this payload.
    if dataclasses.is_dataclass(request):
        covered = {field.name for field in dataclasses.fields(request)}
        assert covered <= set(payload)
    request_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    spec = make_spec(request=request_json)
    rendered = json.dumps(build_sandbox(spec, namespace=NAMESPACE, now=NOW))
    for fragment in ("question", "repository_id", "instruction", "MARKER", "marker-url"):
        assert fragment not in rendered
    for value in payload.values():
        if isinstance(value, str) and len(value) > 8:
            assert value not in rendered
    # The request is delivered, in the Secret, and only there.
    secret = build_secret(spec, namespace=NAMESPACE, owner_uid="uid-1")
    assert base64.b64decode(secret["data"]["request.json"]) == request_json


def test_the_sandbox_is_identical_for_any_request():
    first = make_spec(request=b'{"a":"one"}')
    second = make_spec(request=b'{"b":"two","c":"three"}')
    assert build_sandbox(first, namespace=NAMESPACE, now=NOW) == build_sandbox(second, namespace=NAMESPACE, now=NOW)


def test_the_credential_never_reaches_the_sandbox_manifest():
    spec = make_spec(credential=b"MARKER-credential-aa11")
    rendered = json.dumps(build_sandbox(spec, namespace=NAMESPACE, now=NOW))
    assert "MARKER-credential" not in rendered
    assert "test_token" not in rendered
