# AI jobs architecture and workflow-engine decision

**Status:** proposed, planning only. No executor or workflow engine is deployed by this change.
**Date:** 2026-09-17
**Implementation:** [phased plan](../plans/2026-09-17-ai-jobs-workflows.md)

## Recommendation

Use **Tekton Pipelines as the first execution-backend candidate**, behind the existing
`ai-jobs` boundary. Prove the Git change profile and the webfetch latency/isolation
constraints in a bounded spike before adopting it. Keep `ai-jobs` small: HearthAI
policy and product semantics belong here; task dependency scheduling belongs in
Tekton. Do not build a general workflow language, scheduler, or DAG interpreter.

Keep **n8n optional, above `ai-jobs`**, for a concrete recurring automation that
benefits from its triggers, integrations, and waits. It is not the tool sandbox or
an alternative authorization service. Do not deploy both engines for the first release.

Plain Kubernetes Jobs remain the fallback if Tekton's operational cost or startup
latency outweighs the dependency handling it removes. In that case implement only
the fixed stage sequences required by the shipped profiles. Do not maintain two
production backends until measurements establish a need for both.

These are recommendations for review, not previously approved technology choices.
This design supersedes the single-Job executor assumption in the September 7 plan;
Git remains the first substrate validation profile, followed by webfetch, then research.

## What exists today

Reviewed against main commit `87623b22b7ab82de527365276c293b85f11d44c1`.

| Existing code | What is present | Missing runtime behavior |
| --- | --- | --- |
| `service/ai_jobs/src/ai_jobs/contracts.py` | Run states and typed contracts | Production transport and authenticated caller identity |
| `registry.py`, `tools/git_change.py`, `tools/web_fetch.py` | Closed registry and versioned capability definitions | Repository authorization and credential broker; Git result validation |
| `runs.py` | Validation, request digest, idempotent admission | Transactional dispatch, restart recovery, authorization before dispatch |
| `storage.py` | `RunStore` protocol and in-memory implementation | Durable run, attempt, audit, and publication records |
| `executor.py` | `start(run_id, profile)` and `cancel(run_id)` protocol | Kubernetes implementation, observation, cleanup, request delivery |
| `service/web_fetch/` | Rust fetch and inspection binaries, envelopes, sealed handoff | Separate pods, handoff transport, packaged runtime integration |
| `deploy/charts/hearthai/` | Cohesive application chart | `ai-jobs` runtime and profiles |

The executor currently receives no validated request. Webfetch's stored request
intentionally contains a URL digest, not the URL. An executor cannot reconstruct
that input from the run record. The implementation must solve dispatch and input
lifetime together, rather than adding a Tekton client to `start()` and assuming
recovery already works. Git's result validator is explicitly unimplemented.

## Comparing the choices

This table is an architectural judgment for HearthAI, not a general product ranking.

| Concern | Fixed Kubernetes Jobs | Tekton Pipelines | n8n |
| --- | --- | --- | --- |
| Ephemeral Kubernetes execution | Direct match; HearthAI coordinates stages | TaskRuns create pods; good match for reviewed profiles | Workflow workers are not HearthAI's per-run pod boundary |
| Dependencies, bounded retries, timeouts | Code to write and maintain | Native pipeline facilities | Workflow facilities, but pod lifecycle still needs an executor |
| Git worker then isolated publisher | Explicit sequence in code | Separate Tasks, never two Steps of one Task | Call `ai-jobs`; do not implement publishing in n8n |
| Fetch then offline inspection | Explicit sequence plus handoff | Separate Tasks plus handoff | Call `web_fetch` through `ai-jobs` |
| Human waits, schedules, service integrations | Outside initial executor | Keep long waits outside pod execution | Natural candidate for a later automation layer |
| Dynamic agent reasoning | Fixed worker runs bounded loop | Fixed worker runs bounded loop | Do not migrate HearthAI's agent loop just to gain workflows |
| Additional operations | Kubernetes plus HearthAI persistence | CRDs, controller, upgrades and pruning | Application/database; Redis and workers if using queue mode |
| Custom code still needed | Policy, artifacts, lifecycle and stage sequencing | Policy, artifacts, lifecycle mapping and engine adapter | Same `ai-jobs` substrate, plus automation integration |

Tekton supports dependencies, retries, conditions and final tasks. Task steps run
within a pod: a Step boundary is not a network or credential isolation boundary.
Use separate Tasks for separate trust domains. [Tekton Pipelines](https://tekton.dev/docs/pipelines/pipelines/),
[TaskRuns](https://tekton.dev/docs/pipelines/taskruns/).

n8n queue mode uses Redis and database-backed workers, rather than creating a
fresh Kubernetes pod for each HearthAI capability. Its external task runners
execute Code-node JavaScript/Python; they do not by themselves provide HearthAI's
fixed Git and Rust webfetch profiles. These are reasons to put n8n upstream of
`ai-jobs`, not claims that n8n cannot orchestrate external services.
[n8n queue mode](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode),
[n8n task runners](https://docs.n8n.io/deploy/host-n8n/configure-n8n/set-up-task-runners).

A useful future n8n flow could schedule a repository maintenance request, call an
authorized HearthAI capability, wait for completion, and notify the user. Its Wait
node can resume on time, a webhook, or a form submission. HearthAI still validates
any approval and destination. [n8n Wait](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.wait).

## Ownership and topology

```mermaid
flowchart TD
    UI["Open WebUI adapter"] --> API["ai-jobs policy and run service"]
    N8N["Optional future n8n automation"] -.-> API
    API --> DB["Durable runs and audit"]
    API --> ENGINE["Tekton executor adapter"]
    ENGINE --> TEK["Tekton controller"]
    TEK --> PODS["Fixed ephemeral task pods"]
    PODS --> RESULTS["Bounded artifact and result handoff"]
    RESULTS --> API
```

| Owner | Responsibilities |
| --- | --- |
| Open WebUI / HearthAI session integration | Authenticate the person, carry conversation context, present results and approvals |
| `ai-jobs` | Capability admission, repository authorization, budgets, profile resolution, idempotency, durable run state, credential issuance, result validation and audit |
| Tekton | Execute the admitted fixed task graph, dependency ordering, configured execution retries/timeouts and pod lifecycle |
| Kubernetes / cluster policy | Scheduling, runtime isolation, RBAC, network policy, resource quotas and storage enforcement |
| Workers | One bounded profile operation; no infrastructure choices or authority expansion |
| Optional n8n | Invoke authorized capabilities for a trusted automation identity; manage automation-level triggers and waits |

`ai-jobs` stays the **single HearthAI execution control plane**. Tekton is an
implementation dependency, not a second model-facing job API. The database owns
HearthAI business state and audit; Kubernetes owns observed execution state.
The adapter reconciles them. It does not reschedule Tekton's individual Tasks.

All model-callable tools, including future memory or knowledge tools, must use
this admission boundary when enabled. An Open WebUI built-in or n8n Code node must
not become a backdoor for model-requested execution. Administrative UI actions
and trusted infrastructure controllers are not model-callable tools.

## Small, explicit implementation boundaries

Extend the existing code using composition and domain objects with enforced
invariants. Retain Python for the initial control-plane implementation; using
Tekton is not a reason to rewrite it. Rust webfetch stays language-neutral.
Go remains an option if concrete controller needs justify it later.

- `RunService` handles typed admission and authorization, without Kubernetes details.
- `Run` encapsulates legal transitions, deadlines and terminal-state rules.
- `RunRepository` implements transactions, idempotency and durable dispatch intent.
- `ProfileCatalog` resolves a reviewed version to fixed tasks, images, limits and policy.
- `TektonExecutor` submits, observes and cancels that profile; it does not decide authority.
- `ArtifactStore` owns bounded transfer, finalization, retention and cleanup.
- `RepositoryProvider` resolves authorized repositories and provides materialization and
  publication operations. GitHub is the first adapter; repository IDs stay provider-neutral.

These are responsibility boundaries, not a requirement for a class per bullet or
an extensible plugin framework. Prefer straightforward methods and focused tests.
Do not put profile-name conditionals throughout transport, storage and execution.

The proposed internal executor contract is `ensure_started(execution_spec)`,
`observe(execution_ref)` and `request_cancel(execution_ref)`. An execution spec is
created by trusted code from a frozen profile version and run identity; the public
request never contains it. `ensure_started` is idempotent. Its contents reference
short-lived input, never inline credentials or an arbitrary Kubernetes manifest.

## Admission, dispatch and lifecycle

1. Authenticate the caller and authorize the requested tool and opaque repository ID.
   Resolve the provider, base commit, grants, approval requirements and budget.
2. Atomically create the run, admission audit and dispatch intent. Retain the existing
   caller/tool/version/idempotency-key uniqueness and payload-digest conflict behavior.
3. Dispatch with a deterministic Kubernetes name derived from run ID and attempt.
   On an ambiguous create response, read that name and verify UID/spec identity;
   do not blindly submit a new PipelineRun. Persist the Kubernetes UID and profile digest.
4. Watch and periodically reconcile active executions. Recover a lost watch by listing
   owned resources. Terminal state and result publication use compare-and-set updates.
5. Validate the result envelope or Git publication receipt before committing success.
   A successful PipelineRun alone is insufficient. Cleanup follows durable result capture.

Keep the existing public-neutral states: `accepted`, `starting`, `running`,
`succeeded`, `failed`, `timed_out`, `cancelled`. Add internal execution attempts,
`cancel_requested_at`, cleanup status and publication reconciliation status rather
than pretending every detail is a new public job API.

| Event | HearthAI behavior |
| --- | --- |
| Accepted request awaiting capacity | Persist `accepted`; bounded queue and admission deadline |
| Backend resource created, pods pending | `starting`; queue/startup time consumes the overall deadline |
| Task execution observed | `running` |
| Execution complete with valid successful result | `succeeded` |
| Infrastructure failure, rejected or invalid result | Stable tool-specific failure; preserve operator-only diagnostic detail |
| Cancellation requested | Revoke run grants, request backend cancellation, prevent subsequent publication |
| Cancellation confirmed | `cancelled`; do not claim stopped while pods still execute |
| Overall deadline reached | Revoke grants, record `timed_out`, cancel backend and continue cleanup reconciliation |
| Late result | Cannot overwrite a terminal state; retain any already-created external effect in audit |

Retries have one owner per layer: the dispatcher retries API delivery, Tekton
retries explicitly safe computation within the original deadline, and provider
adapters reconcile ambiguous external writes. Do not automatically retry a whole
Git publication or a model run after a partial side effect. Unknown publication
outcome requires reconciliation before retry. Deadlines and budgets never reset
on a retry; parent research grants and budgets bound any future child fetch runs.

### Input privacy and restart behavior

Durable audit keeps tool-approved storage views. Full webfetch URLs, raw responses,
provider credentials and prompts must not become Tekton params, labels, Results,
exception strings or general logs. Params carry opaque run/input/artifact IDs only.

For the first release, sensitive full inputs live in a bounded volatile input
store until delivered to the worker. The durable dispatch record stores only its
reference. If the control plane restarts before delivery and input is lost, fail
that run deterministically; a new invocation needs a new idempotency key. Do not
claim seamless replay from a digest. Already-started runs can be observed and their
available results recovered. Persisting encrypted short-lived inputs for replay
is a later explicit retention decision, not an accidental prerequisite for Tekton.

## Profiles and artifact boundaries

Profiles are versioned, reviewed application assets. Pin images by digest and freeze
the task specification per attempt. Callers cannot select Tasks, resolvers, images,
commands, mounts, environments, service accounts or network settings. Pass request
data as bounded files, not shell interpolation. Worker pods have no Kubernetes
API token, host mounts or ambient credentials. The trusted controller necessarily
has narrowly scoped Kubernetes API permissions; this does not grant them to workers.

### Git change

The typed request remains `{repository_id, instruction}`. HearthAI authorization
is required for every provider; a selected GitHub App installation is an additional
GitHub-specific condition, not a requirement for every Git server.

A worker Task uses a materializer init container with short-lived read-only access,
then removes credential residue before running repository code in an ephemeral
workspace. Preserve the existing out-of-band credential rules when retaining history.
The worker has no provider token. A separate publisher Task starts only after the
worker pod has terminated and the artifact has been finalized. It refetches the
pinned repository base and applies changes without executing repository code, Git
hooks, filters or submodule commands from that repository.

The publisher gets a short-lived repository-scoped write token only after authority,
approval, cancellation and deadline checks. It uses a run-derived branch and a
persisted publication intent. If a push or PR creation succeeds but the response
is lost, reconcile the expected branch, commit and existing PR before retrying.
Record base/head commits, artifact digest, checks and provider actor. Never merge
or push directly to the protected base branch. A changed base requires an explicit
conflict/rebase policy; do not silently republish different content under an old approval.

### Webfetch

Fetch and inspection are separate Tasks/pods. Fetch has the existing bounded public
HTTP egress policy; inspection has **no egress**, including no artifact-service access.
The existing sealed handoff and separate per-run key mount remain intact until a
separate change deliberately replaces them. Input transfer must finish before the
inspector starts. Output leaves only after inspection completes via trusted collection.

A fetch exit code of `1` represents a recorded public failure which inspection must
still turn into an envelope. A fixed wrapper distinguishes that from exit `2`
(infrastructure failure); a naive success-only DAG edge would lose error envelopes.
Inspection exit `1` likewise has a valid non-success envelope to collect. Neither
case becomes a successful tool result merely because the wrapper completed.

Do not put whole responses into Tekton Results or pod termination messages. Those
are coordination metadata, not the content transport. Raw content stays in bounded
handoff storage and only accepted envelopes reach the model.

### Handoff proposal and explicit policy trade-off

An `emptyDir` cannot be shared across Tasks/pods. Tekton Workspaces do not change
that. [Tekton Workspaces](https://tekton.dev/docs/pipelines/workspaces/).

**Proposed spike transport:** isolated, per-run staging PVCs for bounded artifacts,
with fresh pod-local `emptyDir` workspaces for repository execution. Trusted transfer
Tasks finalize artifacts, copy them to separate consumer claims, and terminate
before consumers start. A publisher never mounts a claim that the Git worker could
write. For webfetch, mount the finalized input read-only in the offline inspector;
use a separate output claim and a later trusted collector Task to submit its envelope.
The collector does not execute artifact contents. Bind manifests to run, attempt,
stage, digest and size; reject cross-run references, path traversal, symlinks and
oversized files. Artifacts are worker-controlled data, not approval or authority.

This is an **explicit proposed exception** to the existing no-durable-volume rule:
run-scoped handoff storage can outlive a pod for transfer/recovery, but is never a
reusable worker workspace. Accept or reject this exception in the spike decision.
No shared household NFS workspace or cross-run claim is allowed. An untrusted
worker may write only its outgoing artifact area, not its consumer's copy. Finalize
only after all producer containers terminate. Storage cleanup requires deleting
both claims and underlying volumes according to the tested reclaim policy; PVC
deletion alone is not proof of data erasure.

The current inspector tries to delete the raw body. For read-only input, add an
explicit executor-managed cleanup mode and put responsibility in the collector/
janitor; do not ignore a failed delete and claim raw data is gone. Preserve the
HMAC key outside all artifact volumes. Limit output/log retention independently.

The extra transfer/collection Tasks are part of the reviewed fixed profile, with
bounded resources and no model-facing operations. This expands the physical pod
count beyond the original two webfetch workers and must be measured against the
existing 25-second overall budget. If this is too slow or storage policy is
unacceptable, stop and record a revised transport/backend decision. Do not quietly
combine fetch and inspection in one pod, enable inspector networking, or increase
the public deadline to make a benchmark pass.

## Deployment and operations

HearthAI packages `ai-jobs`, migrations, profile Tasks/Pipelines, namespaced RBAC,
network policies, quotas, cleanup, images and the Open WebUI adapter. home-ops owns
the cluster-scoped Tekton installation/CRDs and supplies its supported version,
storage class, domains, credentials and existing external Postgres connection.
Use the bundled default-on simple Postgres when external Postgres is not supplied;
use a dedicated database/role for runs. Do not add CNPG to HearthAI or Redis just
for this backend. Install CRDs/controller before application profiles.

Deploy only Tekton Pipelines initially. Triggers, Dashboard, Chains and Results
are not required by this design. Authenticate run/status/cancellation operations;
never expose the Kubernetes API to Open WebUI or n8n. Cluster RBAC scopes the
controller; fixed-spec validation remains necessary because permission to create
a TaskRun can indirectly create a powerful pod.

Use a single active reconciliation leader initially, with database-enforced
idempotency and transactions. Readiness checks cover persistence, backend API,
profile availability and required policy/storage configuration. A missing backend
makes capabilities unavailable; it must not fall back to local execution.

A periodic janitor reconciles abandoned runs, expired inputs, run Secrets, PVCs,
underlying volume cleanup and owned Tekton resources. Final tasks are best effort,
not the only cleanup mechanism after controller crashes or forced deletion.
Keep audit/results before pruning engine objects. Track admission-to-start and
end-to-end latency, failures, cancellation lag, queue depth, cleanup backlog and
resource use without URLs, prompts or arbitrary caller IDs in metric labels.

## Adoption gate and future scope

Adopt Tekton only when the spike demonstrates the boundary tests in the plan,
restart/cancellation behavior, acceptable measured cluster footprint, and the
webfetch budget under representative cold and warm starts. Record versions,
node architecture, storage backend, p50/p95 and failures; do not infer performance
from a laptop or fake executor. Confirm all worker and Tekton images support the
actual target nodes or select an explicit compatible node pool.

If the gate fails, retain the domain contracts and use fixed Kubernetes Jobs;
record precisely which orchestration code is then necessary. The goal is less
custom infrastructure overall, not using an engine at any cost.

n8n remains a follow-on when there is a named automation to ship. Its identity
must be scoped to authorized capabilities and destinations. Repeated workflow
execution must reuse a stable invocation key for the same action; an intentional
new occurrence gets a new key. An n8n wait/form is not proof of HearthAI approval.
Do not keep a sandbox pod alive while waiting for a person. Define durable
asynchronous session delivery and approval semantics before enabling such flows;
this plan does not silently change synchronous webfetch into a public job API.
