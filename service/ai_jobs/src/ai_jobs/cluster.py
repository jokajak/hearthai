"""The narrow Kubernetes surface the executor needs.

``ClusterApi`` is what ``SandboxExecutor`` is written against. ``FakeClusterApi``
is the in-memory stand-in the unit tests use. ``KubernetesClusterApi`` is the
real adapter, on the official client with in-cluster configuration only: the
control plane's own service-account token and nothing else.

Manifests cross this boundary as plain dicts, so the executor and the tests
read exactly what the API server would receive.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

GROUP = "agents.x-k8s.io"
VERSION = "v1beta1"
PLURAL = "sandboxes"

Manifest = dict[str, Any]


class ClusterConflict(Exception):
    """The object already exists."""


class ClusterApi(Protocol):
    def create_sandbox(self, manifest: Manifest) -> Manifest:
        """Create and return the object as stored (with ``metadata.uid``). Raises ClusterConflict."""
        ...

    def get_sandbox(self, name: str) -> Manifest | None: ...

    def list_sandboxes(self, label_selector: str) -> list[Manifest]: ...

    def delete_sandbox(self, name: str) -> None:
        """Idempotent: a missing object is not an error."""
        ...

    def create_secret(self, manifest: Manifest) -> None:
        """Raises ClusterConflict if it exists."""
        ...

    def get_pod(self, name: str) -> Manifest | None:
        """The pod a Sandbox runs. agent-sandbox names it after the Sandbox."""
        ...

    def read_pod_log(self, name: str, limit_bytes: int) -> bytes:
        """At most ``limit_bytes`` bytes from the start of the log, as ``pods/log`` does."""
        ...


class FakeClusterApi:
    """Behaves like the parts of a cluster the executor relies on.

    A sandbox starts out never-started (no conditions). Tests move it with
    ``set_running``, ``finish_succeeded``, ``finish_failed``, ``finish_deadline``
    and ``expire``. Deleting a sandbox deletes the secrets that name its UID as
    an owner, the way garbage collection does.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sandboxes: dict[str, Manifest] = {}
        self.secrets: dict[str, Manifest] = {}
        self.pods: dict[str, Manifest] = {}
        self.logs: dict[str, bytes] = {}
        self.log_requests: list[tuple[str, int]] = []
        self.created_sandboxes = 0
        self.created_secrets = 0
        self.deleted: list[str] = []
        self._uid = 0

    # ClusterApi

    def create_sandbox(self, manifest: Manifest) -> Manifest:
        name = manifest["metadata"]["name"]
        if name in self.sandboxes:
            raise ClusterConflict(name)
        self._uid += 1
        stored = copy.deepcopy(manifest)
        stored["metadata"]["uid"] = f"uid-{self._uid}"
        stored["metadata"]["creationTimestamp"] = _stamp(self.clock())
        stored["status"] = {}
        self.sandboxes[name] = stored
        self.created_sandboxes += 1
        return copy.deepcopy(stored)

    def get_sandbox(self, name: str) -> Manifest | None:
        found = self.sandboxes.get(name)
        return None if found is None else copy.deepcopy(found)

    def list_sandboxes(self, label_selector: str) -> list[Manifest]:
        wanted = dict(part.split("=", 1) for part in label_selector.split(",") if part)
        return [
            copy.deepcopy(item)
            for item in self.sandboxes.values()
            if all(item["metadata"].get("labels", {}).get(k) == v for k, v in wanted.items())
        ]

    def delete_sandbox(self, name: str) -> None:
        found = self.sandboxes.pop(name, None)
        self.pods.pop(name, None)
        self.logs.pop(name, None)
        if found is None:
            return
        self.deleted.append(name)
        uid = found["metadata"]["uid"]
        for secret_name, secret in list(self.secrets.items()):
            if any(ref.get("uid") == uid for ref in secret["metadata"].get("ownerReferences", [])):
                del self.secrets[secret_name]

    def create_secret(self, manifest: Manifest) -> None:
        name = manifest["metadata"]["name"]
        if name in self.secrets:
            raise ClusterConflict(name)
        self.secrets[name] = copy.deepcopy(manifest)
        self.created_secrets += 1

    def get_pod(self, name: str) -> Manifest | None:
        found = self.pods.get(name)
        return None if found is None else copy.deepcopy(found)

    def read_pod_log(self, name: str, limit_bytes: int) -> bytes:
        self.log_requests.append((name, limit_bytes))
        return self.logs.get(name, b"")[:limit_bytes]

    # Test controls

    def set_running(self, name: str) -> None:
        self._status(name, [_condition("Ready", "True", "DependenciesReady")])
        self.pods[name] = {"status": {"phase": "Running"}}

    def finish_succeeded(self, name: str, logs: bytes) -> None:
        self._status(
            name,
            [_condition("Ready", "False", "PodSucceeded"), _condition("Finished", "True", "PodSucceeded")],
        )
        self.pods[name] = {"status": {"phase": "Succeeded"}}
        self.logs[name] = logs

    def finish_failed(self, name: str, logs: bytes = b"") -> None:
        self._status(
            name,
            [_condition("Ready", "False", "PodFailed"), _condition("Finished", "True", "PodFailed")],
        )
        self.pods[name] = {"status": {"phase": "Failed", "reason": "Error"}}
        self.logs[name] = logs

    def finish_deadline(self, name: str) -> None:
        """The kubelet killed the pod at ``activeDeadlineSeconds``."""
        self._status(
            name,
            [_condition("Ready", "False", "PodFailed"), _condition("Finished", "True", "PodFailed")],
        )
        self.pods[name] = {"status": {"phase": "Failed", "reason": "DeadlineExceeded"}}

    def expire(self, name: str) -> None:
        """The controller reached ``shutdownTime`` with the pod unfinished (policy Retain view)."""
        self._status(name, [_condition("Ready", "False", "SandboxExpired")])

    def age(self, name: str, by: timedelta) -> None:
        stored = self.sandboxes[name]["metadata"]
        created = datetime.fromisoformat(stored["creationTimestamp"].replace("Z", "+00:00"))
        stored["creationTimestamp"] = _stamp(created - by)

    def _status(self, name: str, conditions: list[Manifest]) -> None:
        self.sandboxes[name]["status"] = {"conditions": conditions}


def _condition(kind: str, status: str, reason: str) -> Manifest:
    return {"type": kind, "status": status, "reason": reason}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class KubernetesClusterApi:
    """The real adapter. In-cluster configuration only; there is no kubeconfig path."""

    def __init__(self, namespace: str, *, core_api: Any = None, custom_api: Any = None):
        self.namespace = namespace
        if core_api is None or custom_api is None:
            from kubernetes import client, config

            config.load_incluster_config()
            core_api = core_api or client.CoreV1Api()
            custom_api = custom_api or client.CustomObjectsApi()
        self._core = core_api
        self._custom = custom_api

    def create_sandbox(self, manifest: Manifest) -> Manifest:
        try:
            return self._custom.create_namespaced_custom_object(
                GROUP, VERSION, self.namespace, PLURAL, manifest
            )
        except Exception as exc:
            raise _translate(exc) from exc

    def get_sandbox(self, name: str) -> Manifest | None:
        try:
            return self._custom.get_namespaced_custom_object(GROUP, VERSION, self.namespace, PLURAL, name)
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise

    def list_sandboxes(self, label_selector: str) -> list[Manifest]:
        listing = self._custom.list_namespaced_custom_object(
            GROUP, VERSION, self.namespace, PLURAL, label_selector=label_selector
        )
        return list(listing.get("items", []))

    def delete_sandbox(self, name: str) -> None:
        try:
            self._custom.delete_namespaced_custom_object(
                GROUP, VERSION, self.namespace, PLURAL, name, propagation_policy="Foreground"
            )
        except Exception as exc:
            if _status(exc) != 404:
                raise

    def create_secret(self, manifest: Manifest) -> None:
        try:
            self._core.create_namespaced_secret(self.namespace, manifest)
        except Exception as exc:
            raise _translate(exc) from exc

    def get_pod(self, name: str) -> Manifest | None:
        try:
            pod = self._core.read_namespaced_pod(name, self.namespace)
        except Exception as exc:
            if _status(exc) == 404:
                return None
            raise
        return self._core.api_client.sanitize_for_serialization(pod)

    def read_pod_log(self, name: str, limit_bytes: int) -> bytes:
        response = self._core.read_namespaced_pod_log(
            name, self.namespace, limit_bytes=limit_bytes, _preload_content=False
        )
        try:
            return bytes(response.data)
        finally:
            response.release_conn()


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status", None)


def _translate(exc: Exception) -> Exception:
    return ClusterConflict(str(_status(exc))) if _status(exc) == 409 else exc
