"""The real adapter, against stub client objects (no cluster, no network)."""

import pytest

from ai_jobs.cluster import ClusterConflict, KubernetesClusterApi


class ApiError(Exception):
    def __init__(self, status):
        self.status = status


class Stub:
    def __init__(self):
        self.calls = []
        self.raise_status = None
        self.api_client = self

    def _hit(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.raise_status:
            raise ApiError(self.raise_status)

    def create_namespaced_custom_object(self, *args, **kwargs):
        self._hit("create_sandbox", *args, **kwargs)
        return {"metadata": {"uid": "u"}}

    def get_namespaced_custom_object(self, *args, **kwargs):
        self._hit("get_sandbox", *args, **kwargs)
        return {"metadata": {"name": args[-1]}}

    def list_namespaced_custom_object(self, *args, **kwargs):
        self._hit("list", *args, **kwargs)
        return {"items": [{"metadata": {"name": "a"}}]}

    def delete_namespaced_custom_object(self, *args, **kwargs):
        self._hit("delete", *args, **kwargs)

    def create_namespaced_secret(self, *args, **kwargs):
        self._hit("create_secret", *args, **kwargs)

    def read_namespaced_pod(self, *args, **kwargs):
        self._hit("read_pod", *args, **kwargs)
        return "pod"

    def sanitize_for_serialization(self, obj):
        return {"status": {"phase": "Running"}}

    def read_namespaced_pod_log(self, *args, **kwargs):
        self._hit("log", *args, **kwargs)
        return self

    data = b"line\n"

    def release_conn(self):
        self.calls.append(("release", (), {}))


@pytest.fixture
def stub():
    return Stub()


@pytest.fixture
def api(stub):
    return KubernetesClusterApi("ai-jobs", core_api=stub, custom_api=stub)


def test_sandbox_calls_use_the_pinned_group_version_and_plural(api, stub):
    api.create_sandbox({"kind": "Sandbox"})
    api.get_sandbox("x")
    api.delete_sandbox("x")
    api.list_sandboxes("a=b")
    names = [(c[0], c[1][:4]) for c in stub.calls]
    for _, head in names:
        assert head == ("agents.x-k8s.io", "v1beta1", "ai-jobs", "sandboxes")
    assert stub.calls[3][2] == {"label_selector": "a=b"}


def test_conflict_and_not_found_are_translated(api, stub):
    stub.raise_status = 409
    with pytest.raises(ClusterConflict):
        api.create_sandbox({})
    with pytest.raises(ClusterConflict):
        api.create_secret({})
    stub.raise_status = 404
    assert api.get_sandbox("x") is None
    assert api.get_pod("x") is None
    api.delete_sandbox("x")
    stub.raise_status = 500
    with pytest.raises(ApiError):
        api.get_sandbox("x")
    with pytest.raises(ApiError):
        api.delete_sandbox("x")


def test_pod_log_is_read_bounded_as_bytes_and_released(api, stub):
    assert api.read_pod_log("x", 11) == b"line\n"
    name, args, kwargs = stub.calls[0]
    assert kwargs == {"limit_bytes": 11, "_preload_content": False}
    assert stub.calls[-1][0] == "release"


def test_pod_comes_back_as_a_plain_dict(api):
    assert api.get_pod("x") == {"status": {"phase": "Running"}}


def test_adapter_only_loads_in_cluster_configuration():
    import inspect

    source = inspect.getsource(KubernetesClusterApi)
    assert "load_incluster_config" in source
    assert "load_kube_config" not in source
