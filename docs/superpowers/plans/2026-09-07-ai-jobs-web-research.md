# AI Jobs Web Research Implementation Plan

> **2026-09-12 proposed extension:** The [isolated webfetch design](../specs/2026-09-12-isolated-webfetch-design.md) and [implementation plan](2026-09-12-isolated-webfetch.md) supersede this plan's response-handling details, single-pod assumption where stage isolation requires separate pods, and application deployment handoff. They require whole-response rejection, YARA-X inspection, and provenance/action gates before model exposure. The current roadmap's Git-first substrate ordering remains authoritative; references below to research as the first tool are historical.

> **Planning only.** This document defines the implementation sequence for the first
> HearthAI tool. It does not authorize implementing a general job runner or the future
> code-workspace tool.

**Goal:** Deliver one model-facing `web_research` tool through an `ai-jobs` control
plane and an isolated, ephemeral worker, while establishing narrow extension points
that a later tool (likely a read-only code workspace for public repositories) can use
without exposing generic compute controls.

**Architecture:** Open WebUI calls a web-research-specific HTTP contract. Internally,
`ai-jobs` resolves that operation through a typed tool registry, records a generic run,
and asks an executor to start a fixed worker profile. The first executor creates one
Kubernetes Job per run. The worker exchanges messages through a tool-neutral,
job-scoped protocol, but its search, fetch, synthesis, limits, image, and network policy
remain web-research-specific. Extensibility means implementing another reviewed tool
definition and worker profile; it never means accepting an image, command, pod spec,
mount, credential, or arbitrary tool name from a model.

**Implementation scope:** Contracts, control plane, web-research worker, packaging,
tests, and deployment hand-off. The future code-workspace tool is represented only by
interfaces and an extension test fixture. Repository cloning, Git credentials, code
execution, writable workspaces, and code-workspace deployment are not part of this plan.

## Decisions That Must Remain True

1. **Specific API, generic internals.** The public operation is
   `POST /v1/tools/web-research`. There is no public `POST /jobs`, arbitrary tool-name
   route, or Kubernetes-shaped request.
2. **Allowlisted registration.** Every tool is registered in application code with a
   request schema, result schema, worker profile, limits, and failure mapping. Unknown
   tools fail before an execution record or Kubernetes resource is created.
3. **Policy belongs to the tool definition.** Worker image, entry point, service
   account, resource limits, deadline, egress class, and credential grants are
   deployment-controlled values selected by the registered definition, not caller
   input.
4. **Runs are generic; payloads are typed.** Lifecycle, idempotency, cancellation,
   deadlines, audit fields, and terminal delivery are reusable. Research questions,
   sources, citations, and findings stay in the web-research package.
5. **Workers are untrusted and disposable.** A worker receives a single-use run token,
   no Kubernetes token, no provider secret, no durable volume, and no access to shared
   memory. It can invoke only the capabilities granted to that run through `ai-jobs`.
6. **Outbound web access is brokered.** Search, fetch, and LiteLLM requests pass through
   control-plane adapters. Fetch policy rejects private, loopback, link-local, cluster,
   and metadata destinations on every redirect and resolution.
7. **Web content is evidence, not authority.** Fetched text cannot alter limits, request
   credentials, add tools, launch runs, or write memory. Results preserve provenance and
   distinguish supported findings, conflicts, and limitations.
8. **No user-operated job workflow.** Open WebUI receives either a completed result or
   a stable tool failure. Correlation IDs may appear in operator logs, but users and the
   model do not choose images, inspect pods, or poll Kubernetes jobs.
9. **Synchronous MVP with explicit nested deadlines.** Task 0 must confirm or configure
   an Open WebUI tool timeout of **120 seconds** before contracts freeze. The public HTTP
   handler waits at most **110 seconds**, and the Kubernetes worker has
   `activeDeadlineSeconds: 90`, leaving 20 seconds for scheduling/result validation and
   a final 10 seconds for response delivery. If Open WebUI cannot support 120 seconds,
   Task 0 blocks implementation and these budgets are revised together before Task 1.
   Research that misses a boundary fails as `deadline_exceeded`; there is no asynchronous
   continuation in v1.
10. **Depth changes broker budgets, not pod lifetime.** `quick` and `standard` resolve to
    server-owned limits for search calls, fetched bytes, and LiteLLM input/output tokens.
    Both use the fixed 90-second worker deadline; request fields may reduce, never raise,
    server limits.
11. **Retries have a named identity.** The Open WebUI adapter generates a UUID-valued
    `Idempotency-Key` header for each logical tool invocation. The key is scoped to the
    authenticated caller and `web_research:v1`. Reusing it with the same request digest
    attaches to or returns the existing run; reusing it with different content is a
    conflict and never starts another worker.
12. **Cluster and provider ceilings fail closed.** `home-ops` applies namespace
    `ResourceQuota` limits to Job count, CPU, and memory. `ai-jobs` maps quota rejection
    to `temporarily_unavailable`. Per-run broker budgets are backed by deployment-level
    LiteLLM token/spend limits and search-provider usage limits so a model loop cannot
    become unbounded cluster load or spend.

## Proposed Repository Shape

Keep the existing `hearthmem` service independent. Add a separately packaged service
and worker rather than growing memory and tool execution into one process.

```text
service/
├── hearthmem/                         # unchanged service boundary
├── ai_jobs/
│   ├── pyproject.toml
│   ├── src/ai_jobs/
│   │   ├── api.py                     # HTTP transport and web_research route
│   │   ├── auth.py                    # caller and run-token authentication
│   │   ├── contracts.py               # generic Run/Failure wire types
│   │   ├── registry.py                # closed, startup-time tool registry
│   │   ├── runs.py                    # lifecycle and idempotency use cases
│   │   ├── storage.py                 # RunStore protocol + first adapter
│   │   ├── executor.py                # Executor protocol
│   │   ├── kubernetes.py              # fixed-profile Kubernetes adapter
│   │   ├── capabilities.py            # worker capability broker
│   │   ├── observability.py
│   │   └── tools/
│   │       └── web_research.py         # typed definition and policy
│   ├── openapi.json
│   └── tests/
└── web_research_worker/
    ├── pyproject.toml
    ├── src/web_research_worker/
    │   ├── main.py
    │   ├── client.py                  # run-scoped control-plane client
    │   ├── research.py                # bounded research state machine
    │   └── evidence.py                # source and citation assembly
    └── tests/
deploy/charts/ai-jobs/                  # control plane + fixed worker profile
docs/runbooks/ai-jobs.md
```

Do not create a shared `packages/ai-job-contract` package initially. The control-plane
wire contract is published as JSON Schema/OpenAPI and exercised by contract fixtures;
the independently built worker consumes that contract through its client. Extract a
language package only after a second real consumer demonstrates that it is useful.

## Contracts to Freeze First

### Model-facing request

`POST /v1/tools/web-research` accepts:

```json
{
  "question": "What changed in the project this month?",
  "depth": "standard",
  "source_limit": 8
}
```

- `question` is required, trimmed, non-empty, and size-limited.
- `depth` is an enum such as `quick` or `standard`, not an arbitrary token budget.
- `source_limit` is bounded by server policy and may only reduce the server maximum.
- Caller identity and conversation/request correlation come from authenticated headers;
  they are not trusted from model-generated JSON.
- Kubernetes, image, command, environment, URL allowlist, model, credential, timeout,
  and arbitrary capability fields are rejected as unknown properties.

The response is a terminal `WebResearchResult` returned within the synchronous budget.
`Idempotency-Key` is a required integration header generated by the Open WebUI adapter,
not model-authored JSON. If the deadline expires, the response is `deadline_exceeded`;
the public contract has no continuation, callback, polling, or job-management fields.

### Web-research result

```json
{
  "summary": "...",
  "findings": [
    {
      "statement": "...",
      "evidence": [{"source_id": "s1", "support": "..."}],
      "confidence": "high"
    }
  ],
  "sources": [
    {"id": "s1", "url": "https://example.org/page", "title": "...", "retrieved_at": "..."}
  ],
  "conflicts": [],
  "limitations": []
}
```

Every evidence reference must resolve to exactly one source. Source URLs must be the
final validated HTTP(S) URL. The control plane rejects malformed references, duplicate
source IDs, unsupported schemes, oversized fields, and terminal results that exceed
policy.

### Generic internal run

The reusable lifecycle is:

```text
accepted -> starting -> running -> succeeded
                               \-> failed
                               \-> timed_out
                               \-> cancelled
```

A `Run` contains an opaque ID, registered tool key and schema version, timestamps,
deadline, state, request digest, attempt count, and terminal result-or-failure. It does
not contain a caller-selected worker specification. Legal transitions use compare-and-
set semantics so duplicate callbacks and reconciler races cannot overwrite a terminal
result.

### Tool definition and executor boundaries

The interfaces should be structurally equivalent to:

```python
class ToolDefinition(Protocol):
    key: str
    version: int
    worker_profile: str

    def validate_request(self, value: object) -> object: ...
    def validate_result(self, value: object) -> object: ...
    def grants_for(self, request: object) -> frozenset[str]: ...
    def budgets_for(self, request: object) -> "BrokerBudgets": ...
    def public_failure(self, failure: "RunFailure") -> "ToolFailure": ...


class Executor(Protocol):
    def start(self, run: "Run", profile: "ResolvedWorkerProfile") -> None: ...
    def cancel(self, run: "Run") -> None: ...
    def reconcile(self, run: "Run") -> "ExecutionObservation": ...
```

`ResolvedWorkerProfile` is assembled from trusted application/deployment configuration.
It is deliberately absent from every public request. The registry is immutable after
startup and rejects duplicate keys or versions.

### Worker protocol

The worker uses a short-lived, run-bound bearer token to:

- claim its one run and receive the validated tool request plus granted capability names;
- call granted broker operations (`search`, `fetch`, `infer`) with bounded inputs;
- heartbeat without extending the absolute deadline;
- submit one typed terminal result or stable worker failure.

The token cannot read another run, list runs, start work, change grants, or call a
capability after the run is terminal. Request and response bodies are bounded. Retried
claim and terminal submission operations are idempotent.

### Stable failures

Public failures are limited to `invalid_request`, `temporarily_unavailable`,
`deadline_exceeded`, `provider_unavailable`, `unsafe_source`, `incomplete_research`,
and `internal_error`. Kubernetes object names, pod reasons, provider bodies, stack
traces, and secrets remain operator-only and are scrubbed before logging.

## Implementation Tasks

Each task is intended to be independently reviewable and committed before starting the
next. Test doubles come before Kubernetes or external-provider integration so the core
behavior can be verified without a cluster or network.

### Task 0: Spike and freeze the Open WebUI integration envelope

**Files:**

- Create `docs/research/open-webui-web-research-spike.md`
- Create the smallest disposable local adapter fixture needed for the spike; delete it
  unless it is suitable to become the Task 4 adapter test fixture

**Steps:**

- [ ] Verify the exact Open WebUI version and tool-registration mechanism that
  `home-ops` will deploy, including authentication headers and error rendering.
- [ ] Confirm or configure a 120-second tool timeout and measure whether the client or
  reverse proxy applies a shorter timeout. Record commands, versions, and observed
  behavior rather than relying on documentation alone.
- [ ] Verify that the adapter can generate and preserve one `Idempotency-Key` UUID across
  a timed-out/disconnected retry without exposing it to model-generated arguments.
- [ ] Exercise a 110-second fake handler, disconnect/retry behavior, and stable
  `deadline_exceeded` rendering.
- [ ] Freeze the 120/110/90-second nesting and the initial `quick`/`standard` search,
  fetch-byte, and LiteLLM token budgets. If the envelope cannot support them, revise the
  Decisions and contract together before Task 1 starts.

**Acceptance:** The synchronous transport envelope, idempotency behavior, and numeric
budgets are measured and recorded before any public schema is frozen.

### Task 1: Create executable contracts, fixtures, and CI wiring

**Files:**

- Create `service/ai_jobs/pyproject.toml`
- Create `service/ai_jobs/src/ai_jobs/contracts.py`
- Create `service/ai_jobs/src/ai_jobs/tools/web_research.py`
- Create `service/ai_jobs/openapi.json`
- Create `service/ai_jobs/tests/fixtures/web_research/*.json`
- Create `service/ai_jobs/tests/test_contracts.py`
- Create `service/web_research_worker/pyproject.toml`
- Create `service/web_research_worker/tests/test_contract_fixtures.py`
- Modify `.github/workflows/ci.yaml`

**Steps:**

- [ ] Write failing tests for valid minimal/maximal requests, unknown fields, every
  bound, result-reference integrity, serialization round trips, and stable failures.
- [ ] Implement immutable generic run/failure values and web-research-specific request,
  source, finding, evidence, and result validators.
- [ ] Publish only `/healthz`, `/readyz`, and `POST /v1/tools/web-research` in OpenAPI;
  define the required `Idempotency-Key` header there, and keep worker routes in a
  separate non-model-facing schema.
- [ ] Add golden valid and invalid JSON fixtures that the control plane and worker test
  suites will both consume.
- [ ] Assert the public schema contains none of: `image`, `command`, `pod`, `namespace`,
  `environment`, `credential`, `mount`, `workspace`, or arbitrary `tool` selection.
- [ ] Add separate CI install/test steps for `service/ai_jobs` and
  `service/web_research_worker` in this commit. Seed the worker package with a contract-
  fixture test so both commands execute successfully immediately. Every later task must
  land with its own suite already selected by CI; do not wait for Task 9.

**Acceptance:** Contract tests prove malformed or infrastructure-shaped input fails
before execution and every valid result has resolvable evidence.

### Task 2: Implement the closed tool registry and core run service

**Files:**

- Create `service/ai_jobs/src/ai_jobs/registry.py`
- Create `service/ai_jobs/src/ai_jobs/runs.py`
- Create `service/ai_jobs/src/ai_jobs/storage.py`
- Create `service/ai_jobs/src/ai_jobs/executor.py`
- Create `service/ai_jobs/tests/test_registry.py`
- Create `service/ai_jobs/tests/test_runs.py`

**Steps:**

- [ ] Test startup rejection of duplicate definitions, unknown tool keys, illegal state
  transitions, duplicate request idempotency, deadline handling, cancellation races,
  and duplicate worker completion.
- [ ] Implement an immutable startup registry containing only `web_research:v1`.
- [ ] Implement `RunStore` and `Executor` protocols plus deterministic in-memory fakes.
- [ ] Implement the orchestration use case independent of HTTP, Kubernetes, storage,
  LiteLLM, and search vendors.
- [ ] Resolve `grants_for(request)` and `budgets_for(request)` once at admission and
  persist their policy version. `depth` and `source_limit` may vary only broker limits;
  they never change the fixed 90-second worker profile.
- [ ] Add a test-only `example_extension` definition to prove a second typed tool can
  reuse lifecycle code while using different schemas and grants. Do not ship or expose it.

**Acceptance:** Core tests run without network access and demonstrate that adding a tool
requires a registered definition while callers cannot turn the system into a generic
executor.

### Task 3: Add durable state and worker-scoped authentication

**Files:**

- Create `service/ai_jobs/src/ai_jobs/auth.py`
- Extend `service/ai_jobs/src/ai_jobs/storage.py`
- Create `service/ai_jobs/tests/test_auth.py`
- Create `service/ai_jobs/tests/test_storage.py`

**Steps:**

- [ ] Select the first single-replica durable adapter and document its concurrency and
  migration limits before implementing it.
- [ ] Test restart recovery, compare-and-set transitions, idempotency-key uniqueness,
  terminal-result immutability, retention cleanup, and crash points around start/finish.
- [ ] Store hashes of single-use run tokens, bind them to run ID and allowed operations,
  and reject expired, replayed, cross-run, and post-terminal use.
- [ ] Store request/result data only as long as retry and debugging policy needs;
  keep fetched page bodies out of durable storage.
- [ ] Define restart semantics explicitly: the interrupted HTTP caller observes a failed
  request. Reconciliation marks/cancels the orphaned run, releases capacity, and removes
  its Job; it does not attempt deferred result delivery. A retry with the same
  `Idempotency-Key` returns that terminal failure without starting a replacement Job.

**Acceptance:** A restarted service can reconcile non-terminal runs, and a leaked worker
token cannot escape one run or survive terminal completion.

### Task 4: Expose the HTTP API and Open WebUI adapter contract

**Files:**

- Create `service/ai_jobs/src/ai_jobs/api.py`
- Create `service/ai_jobs/src/ai_jobs/__main__.py`
- Create `service/ai_jobs/tests/test_api.py`
- Update `service/ai_jobs/openapi.json`

**Steps:**

- [ ] Add caller authentication, body/time limits, correlation handling, and exact
  content-type behavior before the research route reaches `RunService`.
- [ ] Implement health/readiness separately: health means the process responds;
  readiness means storage and executor prerequisites can accept work.
- [ ] Map internal failures to stable public failures and verify responses never include
  run tokens, provider bodies, Kubernetes details, or tracebacks.
- [ ] Test synchronous success, timeout, disconnect, idempotent retry, capacity failure,
  and cancellation initiated by the orchestration adapter.
- [ ] Require `Idempotency-Key`, scope it to caller plus tool version, attach matching
  retries to the existing run, and reject a key reused with a different request digest.
- [ ] Implement the versioned Open WebUI mechanism measured in Task 0 and keep its
  synchronous timeout assumptions as an adapter concern documented in the runbook.

**Acceptance:** A fake executor can complete a model-facing research call end-to-end,
and generated/checked OpenAPI matches the handler behavior.

### Task 5: Implement the capability broker and SSRF boundary

**Files:**

- Create `service/ai_jobs/src/ai_jobs/capabilities.py`
- Create `service/ai_jobs/src/ai_jobs/providers.py`
- Create `service/ai_jobs/tests/test_capabilities.py`
- Create `service/ai_jobs/tests/test_fetch_policy.py`

**Steps:**

- [ ] Define typed `search`, `fetch`, and `infer` operations with per-run call, byte,
  LiteLLM input/output token, and elapsed-time budgets resolved by `budgets_for`.
- [ ] Test denial of ungranted operations and exhaustion of each budget.
- [ ] Resolve and validate every fetch destination and redirect hop; reject loopback,
  RFC1918/private, link-local, multicast, unspecified, cluster-service, and cloud
  metadata destinations for IPv4 and IPv6.
- [ ] Test DNS rebinding defenses using an injected resolver and connector; validation
  must apply to the address actually connected to, not only an earlier lookup.
- [ ] Limit schemes to HTTP(S), response types to research-safe text formats, redirects,
  decompressed bytes, and parsing time. Do not pass cookies or ambient authorization.
- [ ] Keep provider credentials inside adapters, redact provider errors, and prohibit
  broker responses from returning secrets or raw unrestricted page bodies.
- [ ] Configure dedicated LiteLLM credentials with hard deployment-level token/spend
  ceilings and search-provider credentials with usage limits. Test that exhaustion maps
  to a stable provider/capacity failure and cannot silently fall back to an unlimited
  credential.

**Acceptance:** Adversarial URL and grant tests fail closed, while provider adapters are
replaceable fakes in every non-integration test.

### Task 6: Build the bounded web-research worker

**Files:**

- Create `service/web_research_worker/src/web_research_worker/client.py`
- Create `service/web_research_worker/src/web_research_worker/research.py`
- Create `service/web_research_worker/src/web_research_worker/evidence.py`
- Create `service/web_research_worker/src/web_research_worker/main.py`
- Create `service/web_research_worker/tests/`

**Steps:**

- [ ] Implement a deterministic state machine: plan queries, search, select sources,
  fetch, extract evidence, identify conflicts/gaps, synthesize, validate, submit.
- [ ] Enforce absolute deadline and server-provided budgets locally as defense in depth;
  stop cleanly when useful research cannot be completed.
- [ ] Treat page content as quoted untrusted data in inference prompts and explicitly
  prohibit following embedded instructions or requesting new authority.
- [ ] Preserve source identity through every stage and require findings to cite collected
  evidence rather than model-generated URLs.
- [ ] Test prompt-injection pages, conflicting sources, duplicate/canonical URLs,
  disappearing pages, provider failures, malformed inference output, budget exhaustion,
  cancellation, and terminal retry.
- [ ] Run contract fixtures from Task 1 in the worker suite.

**Acceptance:** With fake search/fetch/inference responses, one worker produces a valid
cited result and predictable failures without filesystem, shell, Kubernetes, memory, or
direct internet access.

### Task 7: Add the fixed Kubernetes executor

**Files:**

- Create `service/ai_jobs/src/ai_jobs/kubernetes.py`
- Create `service/ai_jobs/tests/test_kubernetes.py`
- Create `service/ai_jobs/tests/fixtures/kubernetes/*.json`

**Steps:**

- [ ] Convert only a trusted `web-research-v1` profile into a Kubernetes Job; reject a
  missing/unknown profile at startup rather than accepting caller overrides.
- [ ] Render a fixed image and entry point, run ID plus one-time token reference,
  resources, `activeDeadlineSeconds: 90`, retry count, TTL, labels, and restrictive pod
  security.
- [ ] Disable service-account token mounting, privilege escalation, capabilities, root,
  writable root filesystem, host namespaces, host mounts, and durable volumes.
- [ ] Make creation idempotent by run ID and reconcile missing, pending, running,
  succeeded-without-result, failed, timed-out, and externally deleted Jobs.
- [ ] Map Kubernetes `ResourceQuota` exhaustion during Job creation to
  `temporarily_unavailable`, not `internal_error`.
- [ ] Unit-test Kubernetes errors through an injected client and fixture snapshots; put
  real-cluster behavior in explicitly marked integration tests.

**Acceptance:** Snapshot tests prove public request data cannot affect image, command,
security context, service account, network class, or mounted data.

### Task 8: Add observability without sensitive cardinality

**Files:**

- Create `service/ai_jobs/src/ai_jobs/observability.py`
- Create `service/ai_jobs/tests/test_observability.py`
- Update `service/ai_jobs/src/ai_jobs/api.py`

**Steps:**

- [ ] Emit counters for accepted and terminal runs, histograms for queue/run duration,
  gauges for active runs, and bounded labels for tool version and failure category.
- [ ] Add structured audit events for state transitions and policy denials.
- [ ] Test that questions, user/conversation IDs, URLs, citations, page content, tokens,
  and credentials never become metric labels or ordinary log fields. Run IDs are also
  forbidden as metric labels, but are permitted in structured operational logs.
- [ ] Use the opaque run ID to join control-plane, Kubernetes, and worker log events for
  reconciliation. Never expose it to the model-facing result.

**Acceptance:** Tests inspect emitted metrics/log records and enforce both cardinality
and redaction rules.

### Task 9: Package and harden release artifacts

**Files:**

- Create `service/ai_jobs/Dockerfile`
- Create `service/web_research_worker/Dockerfile`
- Modify `service/.dockerignore`
- Create `deploy/charts/ai-jobs/Chart.yaml`
- Create `deploy/charts/ai-jobs/values.yaml`
- Create `deploy/charts/ai-jobs/templates/`
- Modify `.github/workflows/ci.yaml`
- Modify `.github/workflows/release.yaml`

**Steps:**

- [ ] Build separate pinned control-plane and worker images as non-root with read-only
  root filesystems and no unnecessary packages.
- [ ] Keep the existing flat, import-from-`service/` `hearthmem` package unchanged while
  the two new independently packaged services use `src/` layouts. Document this mixed
  layout, and add `ai_jobs/` and `web_research_worker/` to `service/.dockerignore` so the
  existing `context: ./service` hearthmem image does not absorb either new tree.
- [ ] Chart only trusted worker profiles; values may pin released images/resources but
  must not become model-facing configuration.
- [ ] Create the control-plane ServiceAccount/Role with only the Job/Pod operations
  required in the dedicated namespace. Worker pods use a tokenless ServiceAccount.
- [ ] Add chart schema and CI assertions for pod security, token automount, deadlines,
  TTL, RBAC scope, separate identities, and absence of secret env vars in worker specs.
- [ ] Extend CI/release jobs without weakening existing `hearthmem` tests, image smoke
  tests, or chart checks.
- [ ] Keep one repository release version: a `vX.Y.Z` tag publishes three images
  (`hearthmem`, `ai-jobs`, `web-research-worker`) and two charts (`hearthmem`, `ai-jobs`),
  all with the same `X.Y.Z` image/chart/app version. Give each image its own metadata and
  build context and package/push each chart explicitly; do not reuse the current
  single-image metadata output across images.

**Acceptance:** A single versioned release contains all three images and both charts;
rendered `ai-jobs` manifests show least privilege and a fixed web-research profile.

### Task 10: Document the home-ops hand-off and verify end to end

**Files:**

- Create `docs/runbooks/ai-jobs.md`
- Modify `docs/ARCHITECTURE.md` after implementation status changes (approved direction
  and scope corrections may be documented before then)
- Modify `docs/ROADMAP.md` after milestone acceptance passes (approved direction and
  scope corrections may be documented before then)

**Steps:**

- [ ] Document required `home-ops` resources: dedicated namespace, chart pin, persistent
  control-plane state, External Secrets, default-deny policies, explicit DNS/control-
  plane/provider egress, Open WebUI registration, metrics scraping, and namespace
  `ResourceQuota` caps for Job count plus aggregate CPU and memory.
- [ ] Document install, upgrade, rollback, token rotation, stuck-run reconciliation,
  retention cleanup, provider outage, and credential-compromise procedures.
- [ ] Run an end-to-end current-information question from Open WebUI and confirm a cited
  response, ephemeral worker termination, and no user-visible polling workflow.
- [ ] Exercise malicious redirects, prompt injection, malformed results, worker crash,
  provider outage, deadline, duplicate delivery, and control-plane restart.
- [ ] Inspect a live worker: no Kubernetes/provider credentials, service-account token,
  durable volume, writable root, forbidden egress, or access to `hearthmem`.

**Acceptance:** The deployed feature behaves like one research tool, operational failure
is diagnosable without a dashboard, and canonical docs are updated from “planned” only
when the evidence exists.

## Extension Procedure for a Future Tool

This is the only future-tool structure this plan commits to. A future code-workspace
proposal must supply all of the following in a separate design and threat review:

1. a new, specific model-facing request/result contract;
2. a registered `ToolDefinition` with a new key and schema version;
3. a fixed reviewed worker profile and separate image;
4. the minimum named broker capabilities it needs;
5. tool-specific network, filesystem, credential, size, and time policy;
6. contract, policy, worker, executor, chart, and adversarial tests;
7. stable public failure mappings and redaction rules.

For the likely code-workspace tool, that later design must decide clone URL validation,
redirects, repository-size and object-count limits, submodules/LFS, decompression bombs,
Git protocol restrictions, commit pinning, symlinks, generated files, cleanup, and
whether analysis is strictly read-only. None of those decisions should be smuggled into
the web-research implementation, and no workspace, Git, shell, or code execution
capability is added by this plan.

## Verification Matrix

Before declaring web research complete, preserve evidence for these layers:

| Layer | Required checks |
|---|---|
| Contract | Golden request/result fixtures, bounds, unknown fields, evidence integrity, OpenAPI conformance |
| Core | Registry closure, state transitions, idempotency, races, deadlines, restart recovery |
| Security | Run-token scope, SSRF/redirect/rebinding suite, capability grants, secret and log redaction |
| Worker | Injection corpus, conflicting evidence, provider failures, cancellation, malformed output |
| Kubernetes | Fixed-profile snapshots, least-privilege RBAC, pod security, cleanup/reconciliation |
| Packaging | Both images build/smoke, chart lint/render/schema checks, existing hearthmem CI unchanged |
| End to end | Open WebUI invocation, cited response, hidden lifecycle, live pod inspection, failure drills |

## Explicit Non-Goals

- implementing the code-workspace tool;
- asynchronous continuation, callback delivery, or user/model-visible polling;
- cloning any Git repository;
- shell, terminal, Python, package-manager, browser-session, or arbitrary code execution;
- caller-selected tools, images, commands, models, credentials, limits, mounts, or pods;
- a generic public jobs API, queue product, dashboard, scheduler, recurring work, or job history;
- direct worker access to Kubernetes, provider credentials, HearthAI memory, or private networks;
- automatic writes of web-derived content to shared or private memory;
- multiple control-plane replicas or zero-downtime migration in the first increment.

## Completion Criteria

Implementation is complete only when:

- Open WebUI can invoke the explicit `web_research` operation from a normal conversation;
- one isolated worker is created for one run and always reaches a terminal/cleaned state;
- validated findings cite sources and report conflicts and limitations;
- lifecycle details remain hidden from users and model-generated requests;
- network, credential, token, storage, and Kubernetes boundaries pass adversarial tests;
- aggregate health and metrics are useful without sensitive or high-cardinality fields;
- the registry and executor seams are proven by a test-only second definition, while no
  second production tool or generic execution API exists;
- existing `hearthmem` behavior and release guarantees remain green; and
- `home-ops` owns only deployment, secrets, policy wiring, Open WebUI registration, and
  observability—not HearthAI tool behavior or contracts.
