# ai-jobs executor, sandbox namespace and memory recall

**Status:** proposed, for owner approval (gate G5). Nothing here is implemented.
**Date:** 2026-11-14.
**Plan items:** I12 (executor skeleton and sandbox namespace) and I13 (memory recall, exposed) of
[`2026-11-13-local-first-increments.md`](../plans/2026-11-13-local-first-increments.md), Milestone 6.
Package S3 of [the orchestration brief](../plans/2026-11-13-local-first-orchestration.md).
**home-ops side:** "Sandbox namespace (hearthai I12)" in
`home-ops/docs/plans/2026-11-13-hearthai-local-inference.md`.

This spec settles the four things the brief asks for: the executor's Kubernetes interface, the
sandbox namespace inputs home-ops provides first, the `memory-recall-v1` profile, and how a store
token reaches one run. It ends with the packages it breaks into. The standing rules in
`docs/ARCHITECTURE.md` "Execution substrate" are not reopened: typed request, fixed profile,
ephemeral pods, no service-account token, no ambient credentials, durable audit.

## Decisions to approve

| # | Decision | Alternative not taken, and why |
|---|---|---|
| E1 | **The executor creates one Kubernetes `Job` per run** (`backoffLimit: 0`, `activeDeadlineSeconds` from the profile, `ttlSecondsAfterFinished: 300`). | **Tekton** (proposed in the open jokajak/hearthai#15): a controller, CRDs and upgrades to run, for profiles that today are one pod. #15 itself keeps plain Jobs as the fallback. A two-stage profile (Git worker then publisher, fetch then inspect) is two Jobs started in sequence by code, which is short. Revisit Tekton only when a profile needs a graph that code sequencing makes hard. **Bare Pods**: nothing cleans up when `ai-jobs` dies mid-run; a Job's TTL controller does. |
| E2 | **Inputs reach a run through one per-run `Secret`**, owned by the Job (ownerReference), mounted read-only at `/run/ai-jobs/`. It holds `request.json` and, when the profile has one, `credential`. Nothing else enters the pod: no environment from the request, no arguments from the request. | Environment variables (visible in the pod spec and to anything that can read it); a callback to `ai-jobs` (needs egress from the sandbox to the control plane). The Secret is garbage-collected with the Job, so cleanup does not depend on `ai-jobs` being alive. |
| E3 | **The result is the last line of the worker's stdout**: one envelope v1 JSON object, read through `pods/log` with a byte limit once the pod has terminated, then validated by the tool definition. | Termination message (4 KiB cap, too small for recall); a result volume (needs a PVC per run, see #15's staging PVCs); a callback (as above). Logs need no egress and no storage; the worker is untrusted either way and the envelope is validated. |
| E4 | **Run records and audit live in SQLite** on one `ReadWriteOnce` PVC mounted by the single-replica `ai-jobs` Deployment, backed up by VolSync like `hearthmem-data`. | Postgres: the shared cluster would need a new database and role for a store that sees a few writes an hour. SQLite is what hearthmem already does. Losing in-flight run state on a pod restart is accepted (home-lab rule); losing audit is not, hence the backup. |
| E5 | **hearthmem gains read-only tokens.** A store's write token can mint one read token; the service keeps only its digest; read tokens can `GET` a store and search it and are refused on every write route. Recall pods only ever hold a read token. | Giving the recall pod the store token: today that token can write (`POST /stores/{token}/entries`), so a compromised or confused worker could plant entries that later reach every chat. A Cilium L7 rule allowing only `GET`: depends on the cluster's Cilium L7 proxy, which this repository cannot test, and enforces the rule outside the code that owns it. |
| E6 | **Callers are identified by a static per-consumer key** (`Authorization: Bearer`), mapped to a `caller_id` in configuration. The first consumer is Open WebUI. | Forwarding the Open WebUI user: needs `ai-jobs` to verify Open WebUI sessions. Not needed while the shared store is household-wide; revisit with I14 (writes), where approval needs a person. |
| E7 | **One image for the control plane and the workers**, `ghcr.io/jokajak/ai-jobs`, with separate entry points (`ai-jobs serve`, `ai-jobs-worker memory-recall`). The worker entry point imports only the stdlib and `ai_jobs.envelope`. | One image per worker: more builds and pins for a 60-line worker. Code that is present is not authority; the pod has no token, no credential beyond its own read token, and egress only to hearthmem. |
| E8 | **Model-callable endpoints wait for the result.** `POST /v1/tools/memory-recall` admits the run, waits up to the profile deadline, and returns the validated envelope or a fixed failure. The run's cleanup does not depend on the caller staying connected. | An asynchronous run API with polling: Open WebUI tool servers are synchronous OpenAPI calls. The async shape can be added for n8n later without changing the executor. |

## Executor interface

`executor.py` today has `start(run_id, profile)` and `cancel(run_id)`, and `RunService.admit` never
passes the validated request to it, so no executor could deliver an input. The contract becomes
(names from #15):

```python
@dataclass(frozen=True)
class ExecutionSpec:          # built only by trusted code, from a frozen profile and a run
    run_id: str
    profile: ResolvedWorkerProfile
    request_json: bytes       # tool-validated request, canonical JSON
    credential: bytes | None  # resolved by the profile, never supplied by a caller

class Executor(Protocol):
    def ensure_started(self, spec: ExecutionSpec) -> ExecutionRef: ...   # idempotent per run_id
    def observe(self, ref: ExecutionRef) -> Observation: ...             # pending | running | succeeded(stdout_tail) | failed(reason) | timed_out
    def request_cancel(self, ref: ExecutionRef) -> None: ...
```

`ResolvedWorkerProfile` grows to everything the pod spec needs, all from reviewed code and chart
values, none from the request: `name`, `image`, `entrypoint`, `active_deadline_seconds`,
`resources`, `credential_source` (a named key of the control plane's own Secret, or none),
`result_limit_bytes`. A test asserts that no field of any request type can reach the rendered
`Job` (the I12 requirement).

`KubernetesJobExecutor` implements it against a narrow `ClusterApi` protocol (create Job, create
Secret with ownerReference, read Job and Pod status, read pod log with `limitBytes`, delete Job
with foreground propagation). Unit tests use an in-memory fake `ClusterApi`. The real adapter uses
the official `kubernetes` Python client with the in-cluster service-account token of the
control-plane pod only.

**The Job every run gets:**

- namespace `ai-jobs`; labels `app.kubernetes.io/managed-by: ai-jobs`, `ai-jobs.hearthai/profile:
  <name>`, `ai-jobs.hearthai/run: <id>`. The profile label is what the egress policy selects.
- pod: `automountServiceAccountToken: false`, `serviceAccountName: default` in a namespace where
  `default` has no bindings, `enableServiceLinks: false`, `restartPolicy: Never`,
  `runAsNonRoot`, fixed uid/gid, seccomp `RuntimeDefault`, `readOnlyRootFilesystem`, all
  capabilities dropped, no privilege escalation, no host namespaces or host paths. Volumes: the
  per-run Secret (read-only, mode 0400) and one small `emptyDir` at `/tmp`.
- no `env` beyond a fixed `HOME=/tmp`; `command` is the profile's entry point with no arguments.

**Run lifecycle.** `admit` (unchanged validation and idempotency) → store `accepted` →
`ensure_started` → `starting` → observed `running` → `succeeded` with a validated envelope, or
`failed` / `timed_out` with a fixed failure code. Every transition is an audit row: run id, caller,
tool and version, request digest, profile, state, time, failure code. The audit never stores the
request body or the result body; tools already define a `storage_view`.

**When `ai-jobs` restarts.** At startup it (1) marks every non-terminal run older than its
profile's deadline as `failed` with `internal_error`, and (2) deletes `ai-jobs`-managed Jobs older
than the longest deadline plus a grace period. That is the whole recovery story; reconciliation,
replay and failover stay deferred as #15 proposed.

## Sandbox namespace: what home-ops provides first

Change C in the home-ops companion. It must be live, and checked, before the value that enables
`ai-jobs` merges: the default-deny policy is read from the live cluster, and a sandbox pod that
starts before it exists has open egress.

| Object | Content |
|---|---|
| `Namespace ai-jobs` | Labels `pod-security.kubernetes.io/enforce: restricted` (and `warn`, `audit`). |
| `ResourceQuota` | `pods: 4`, `requests.cpu: 1`, `requests.memory: 1Gi`, `limits.memory: 2Gi`, `count/jobs.batch: 8`, `count/secrets: 16`. Generous for one household; bounds a runaway loop. |
| `LimitRange` | Container default and max: `cpu 500m`, `memory 256Mi`. |
| `CiliumNetworkPolicy` default deny | `endpointSelector: {}` with empty `ingress` and `egress`: nothing in, nothing out, DNS included. Per-profile allows come from the chart (below). |
| `Role` + `RoleBinding` | For the service account `hearthai-ai-jobs` in namespace `ai`: `batch/jobs` create, get, list, watch, delete; `pods` get, list, watch; `pods/log` get; `secrets` create, delete. Nothing cluster-scoped, nothing in any other namespace. |
| `default` service account | No bindings. `automountServiceAccountToken: false` on it as well. |

The hearthai chart renders, into `ai-jobs`, one `CiliumNetworkPolicy` per enabled profile. For
`memory-recall-v1`: egress to `kube-dns` on 53/UDP and 53/TCP, and to the hearthmem pods of this
release on their port. Nothing else. The control plane's own pod in `ai` gets an ingress policy
admitting only Open WebUI pods of the release (Meridian's pattern) and node probes.

## `memory-recall-v1`

**Tool:** `memory_recall` v1. Request: `{"query": string 1..500, "limit": int 1..10, default 5}`;
unknown fields refused. Storage view: query digest and limit, not the query.

**Worker:** reads `/run/ai-jobs/request.json` and `/run/ai-jobs/credential` (a hearthmem read
token), sends one `GET /stores/-/entries?q=…&limit=…` with the token only in `X-Store-Token` (the
server prefers the header and ignores the path segment then; a bare `/stores/entries` would hit the
describe route) to the hearthmem Service named in its profile, and prints one envelope: `status: ok`, `trust: household_shared` (a new
trust value: written by household members, not external, still not instructions), `data:
{"entries": [{"content", "author", "created_at", "tags"}]}` with per-entry and total size limits.
Errors map to fixed envelope codes; no hearthmem error text reaches the model.

**Profile:** deadline 15 s, `cpu 100m/200m`, `memory 64Mi/128Mi`, result limit 64 KiB, credential
source `memory_recall_read_token` from the control plane's Secret.

**How the token reaches one run.** The owner mints a read token once (owner step, below) and
stores it in Bitwarden. An `ExternalSecret` in `ai` puts it in the control plane's Secret. For each
recall run the executor copies it into that run's Secret in `ai-jobs`, mounted only into that run's
pod, and deleted with the Job. The token is never in a pod spec, an environment, a log line or the
audit. A store holds one read token at a time; minting a new one revokes the old.

**Exposure (I13).** Open WebUI registers `ai-jobs` as the single entry in
`TOOL_SERVER_CONNECTIONS`, with the caller key. Because that JSON carries a key, the chart takes it
from a Secret rather than the ConfigMap. P3's deny-table test changes in the same package: the only
permitted non-empty value is exactly this entry. **Lane rule** (from the plan, restated because it
is the only privacy control here): the shared store holds nothing that may not reach a cloud model,
because a cloud chat can recall it. The recall tool is available in every chat, private ones
included; a private chat only reads.

## What this does not do

- No write tool (I14 needs its own spec: approval, publisher).
- No webfetch on the executor (I15, after the lane-gate spike). E2 and E3 are chosen so webfetch can
  reuse them: its URL travels in the run Secret, never in a durable record.
- No per-user identity, no async API, no restart reconciliation, no Tekton.
- No hearthmem ingress policy. hearthmem today admits any in-cluster client holding a token; that is
  unchanged, and worth its own small change once its consumers are listed.

## Verification

- **Unit (no cluster):** executor against the fake `ClusterApi`, including idempotent
  `ensure_started`, cancellation, deadline, oversized and malformed results, startup sweep; the
  "no request field reaches the Job" test; SQLite store transitions and audit rows; hearthmem
  read-token rules (mint, use, refused on writes, rotation).
- **Chart:** with `aiJobs.enabled` false, the render is identical to before. Enabled: the control
  plane has no host mounts and a read-only root; the profile policy allows exactly DNS and
  hearthmem; the `TOOL_SERVER_CONNECTIONS` value comes from a Secret.
- **End to end on kind (CI):** a kind cluster with the home-ops objects applied from a fixture, the
  chart installed, hearthmem seeded, one recall run through the HTTP API: the Job is admitted under
  `restricted`, the envelope comes back, the Job and its Secret are gone after the TTL, and a
  request with an extra field is refused before anything is created. kind's default CNI does not
  enforce `CiliumNetworkPolicy`, so this proves lifecycle, admission and cleanup, not isolation.
  Say so in the test.
- **Owner, once live:** a recall from an Open WebUI chat returns seeded entries; `kubectl get
  jobs,secrets -n ai-jobs` is empty a few minutes later; a sandbox pod cannot reach anything but
  hearthmem (owner runs one `curl` from a debug Job with the profile label).

## Order across repositories

1. hearthai: hearthmem read tokens (A1) merged and released (a hearthmem tag; the release workflow
   owns that image pin).
2. hearthai: control plane, worker, chart (A2–A4), `aiJobs.enabled: false` by default. No-op for
   the cluster.
3. home-ops change C (A5) merged; owner checks `kubectl get cnp,quota,role -n ai-jobs`.
4. **Only then** home-ops enables `aiJobs` with the no-op profile (A6). Owner runs one no-op run.
5. Owner steps for recall, then A7 (recall exposed in Open WebUI), which also needs P3 merged.

## Owner steps (one-off)

- Create the first household store with the hearthmem CLI, keep its write token in Bitwarden, seed a
  few entries that are safe to reach a cloud model.
- Mint the store's read token (`hearthmem read-token <store>` after A1) and put it in Bitwarden.
- Put the Open WebUI caller key in Bitwarden. Item and property names are given in A5.

## Packages

Appended to the orchestration brief once this spec is approved. Each is one executor brief.

| ID | Package | Repo | Depends on |
|---|---|---|---|
| A1 | hearthmem read-only tokens: store, server, CLI, tests | hearthai | G5 |
| A2 | Executor contract, `KubernetesJobExecutor` on a fake `ClusterApi`, SQLite run store and audit, startup sweep, `noop-v1` profile | hearthai | G5 |
| A3 | HTTP surface (`/v1/tools/memory-recall`, caller keys), `memory_recall` tool definition, recall worker, envelope trust value | hearthai | A2, A1 |
| A4 | `ai-jobs` image and CI build; chart templates behind `aiJobs.enabled`; chart tests; kind end-to-end job | hearthai | A3 |
| A5 | Sandbox namespace, quota, limit range, default deny, Role, ExternalSecret | home-ops | G5; owner confirms live before A6 |
| A6 | Enable `aiJobs` with `noop-v1` only | home-ops | A4 merged, A5 live |
| A7 | Recall exposed: profile enabled, Open WebUI registration from a Secret, deny-table test updated | hearthai, then home-ops | A6 live, P3 merged, owner steps |
