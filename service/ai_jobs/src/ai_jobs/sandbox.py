"""One run, one agent-sandbox ``Sandbox``.

``build_sandbox`` and ``build_secret`` are pure: from a trusted
:class:`ExecutionSpec` (plus the namespace and the time) to the manifests the
cluster receives. The ``Sandbox`` is built from the profile and the run id
alone; the request's bytes appear only in the per-run Secret, which is the only
channel into the pod.

Field names are agent-sandbox v1.0.9, ``api/v1beta1/sandbox_types.go``:
``spec.podTemplate`` (``spec`` and ``metadata``), and the inlined
``failedPodPolicy``, ``service``, ``shutdownTime``, ``shutdownPolicy``.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta

from ai_jobs.cluster import GROUP, VERSION, ClusterApi, ClusterConflict, Manifest
from ai_jobs.executor import (
    ExecutionRef,
    ExecutionSpec,
    FailureReason,
    Observation,
    ObservedState,
)

NAMESPACE = "ai-jobs"
API_VERSION = f"{GROUP}/{VERSION}"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
PROFILE_LABEL = "ai-jobs.hearthai/profile"
RUN_LABEL = "ai-jobs.hearthai/run"
MANAGED_SELECTOR = f"{MANAGED_BY_LABEL}=ai-jobs"

#: ``shutdownTime`` is the deadline plus this, so the controller's cleanup runs
#: even if ai-jobs has died, but never before the pod's own deadline.
SHUTDOWN_GRACE_SECONDS = 60

MOUNT_DIR = "/run/ai-jobs"
REQUEST_FILE = "request.json"
CREDENTIAL_FILE = "credential"

#: Fixed identity the worker runs as. The image must contain nothing that needs more.
RUN_AS = 65532

_RUN_ID = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")


def object_name(run_id: str) -> str:
    """The Sandbox, its pod and its Secret share this name. Run ids are server-made."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run id is not usable as a Kubernetes object name")
    return f"ai-job-{run_id}"


def build_sandbox(spec: ExecutionSpec, *, namespace: str, now: datetime) -> Manifest:
    profile = spec.profile
    labels = {
        MANAGED_BY_LABEL: "ai-jobs",
        PROFILE_LABEL: profile.name,
        RUN_LABEL: spec.run_id,
    }
    shutdown = now.astimezone(UTC) + timedelta(seconds=profile.active_deadline_seconds + SHUTDOWN_GRACE_SECONDS)
    name = object_name(spec.run_id)
    pod_spec: Manifest = {
        "automountServiceAccountToken": False,
        "serviceAccountName": "default",
        "enableServiceLinks": False,
        "restartPolicy": "Never",
        "activeDeadlineSeconds": profile.active_deadline_seconds,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": RUN_AS,
            "runAsGroup": RUN_AS,
            # With fsGroup the kubelet makes the Secret's files group-readable, which is how
            # a non-root worker can read a 0400 file. Checked on a real cluster in A4.
            "fsGroup": RUN_AS,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "worker",
                "image": profile.image,
                "imagePullPolicy": "IfNotPresent",
                "command": list(profile.entrypoint),
                "env": [{"name": "HOME", "value": "/tmp"}],
                "resources": {
                    "requests": {"cpu": profile.resources.cpu_request, "memory": profile.resources.memory_request},
                    "limits": {"cpu": profile.resources.cpu_limit, "memory": profile.resources.memory_limit},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "privileged": False,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [
                    {"name": "run", "mountPath": MOUNT_DIR, "readOnly": True},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }
        ],
        "volumes": [
            {"name": "run", "secret": {"secretName": name, "defaultMode": 0o400}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}},
        ],
    }
    if profile.runtime_class is not None:
        pod_spec["runtimeClassName"] = profile.runtime_class
    return {
        "apiVersion": API_VERSION,
        "kind": "Sandbox",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(labels)},
        "spec": {
            "podTemplate": {"metadata": {"labels": dict(labels)}, "spec": pod_spec},
            "service": False,
            "failedPodPolicy": "Retain",
            "shutdownTime": shutdown.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "shutdownPolicy": "Delete",
            "operatingMode": "Running",
        },
    }


def build_secret(spec: ExecutionSpec, *, namespace: str, owner_uid: str) -> Manifest:
    """The only place the request (and a credential) is written."""
    name = object_name(spec.run_id)
    data = {REQUEST_FILE: _b64(spec.request_json)}
    if spec.credential is not None:
        data[CREDENTIAL_FILE] = _b64(spec.credential)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "immutable": True,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                MANAGED_BY_LABEL: "ai-jobs",
                PROFILE_LABEL: spec.profile.name,
                RUN_LABEL: spec.run_id,
            },
            "ownerReferences": [
                {
                    "apiVersion": API_VERSION,
                    "kind": "Sandbox",
                    "name": name,
                    "uid": owner_uid,
                    "controller": False,
                    "blockOwnerDeletion": False,
                }
            ],
        },
        "data": data,
    }


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class SandboxExecutor:
    """``Executor`` on a :class:`ClusterApi`."""

    def __init__(
        self,
        api: ClusterApi,
        *,
        namespace: str = NAMESPACE,
        clock=lambda: datetime.now(UTC),
    ):
        self.api = api
        self.namespace = namespace
        self.clock = clock

    def ensure_started(self, spec: ExecutionSpec) -> ExecutionRef:
        name = object_name(spec.run_id)
        sandbox = self.api.get_sandbox(name)
        if sandbox is None:
            try:
                # The Sandbox goes first: the Secret's ownerReference needs its UID.
                sandbox = self.api.create_sandbox(build_sandbox(spec, namespace=self.namespace, now=self.clock()))
            except ClusterConflict:
                sandbox = self.api.get_sandbox(name)
                if sandbox is None:
                    raise
        try:
            self.api.create_secret(
                build_secret(spec, namespace=self.namespace, owner_uid=sandbox["metadata"]["uid"])
            )
        except ClusterConflict:
            pass
        except Exception:
            # No pod can start without its input; do not leave a Sandbox waiting for one.
            self.api.delete_sandbox(name)
            raise
        return ExecutionRef(run_id=spec.run_id, name=name, result_limit_bytes=spec.profile.result_limit_bytes)

    def observe(self, ref: ExecutionRef) -> Observation:
        sandbox = self.api.get_sandbox(ref.name)
        if sandbox is None:
            return Observation(ObservedState.FAILED, FailureReason.SANDBOX_GONE)
        conditions = {c.get("type"): c for c in sandbox.get("status", {}).get("conditions", [])}
        finished = conditions.get("Finished")
        if finished is not None and finished.get("status") == "True":
            if finished.get("reason") == "PodSucceeded":
                return self._read_result(ref)
            pod = self.api.get_pod(ref.name)
            if pod is not None and pod.get("status", {}).get("reason") == "DeadlineExceeded":
                return Observation(ObservedState.TIMED_OUT)
            return Observation(ObservedState.FAILED, FailureReason.POD_FAILED)
        ready = conditions.get("Ready")
        if ready is not None:
            if ready.get("reason") == "SandboxExpired":
                return Observation(ObservedState.TIMED_OUT)
            if ready.get("status") == "True":
                return Observation(ObservedState.RUNNING)
        return Observation(ObservedState.PENDING)

    def request_cancel(self, ref: ExecutionRef) -> None:
        self.api.delete_sandbox(ref.name)

    def sweep_stale(self, *, now: datetime, longest_deadline_seconds: int) -> list[str]:
        """Startup: delete ai-jobs Sandboxes older than the longest deadline plus the grace.

        In-flight runs are forgotten when the process restarts; this is all the recovery there is.
        """
        cutoff = now - timedelta(seconds=longest_deadline_seconds + SHUTDOWN_GRACE_SECONDS)
        removed = []
        for item in self.api.list_sandboxes(MANAGED_SELECTOR):
            created = item["metadata"].get("creationTimestamp")
            if not created:
                continue
            if datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
                self.api.delete_sandbox(item["metadata"]["name"])
                removed.append(item["metadata"]["name"])
        return removed

    def _read_result(self, ref: ExecutionRef) -> Observation:
        # limitBytes counts from the start of the log. One byte over the limit is enough to
        # know the output is too big; a worker prints one line, so its whole output is read.
        raw = self.api.read_pod_log(ref.name, ref.result_limit_bytes + 1)
        if len(raw) > ref.result_limit_bytes:
            return Observation(ObservedState.FAILED, FailureReason.RESULT_TOO_LARGE)
        lines = [line for line in raw.splitlines() if line.strip()]
        if not lines:
            return Observation(ObservedState.FAILED, FailureReason.NO_RESULT)
        try:
            result = json.loads(lines[-1])
        except ValueError:
            return Observation(ObservedState.FAILED, FailureReason.MALFORMED_RESULT)
        return Observation(ObservedState.SUCCEEDED, result=result)
