# AI jobs workflow implementation plan

**Status:** proposed; documentation only, no runtime deployment authorized by this plan.
**Design:** [AI jobs architecture and engine decision](../specs/2026-09-17-ai-jobs-workflows-design.md)
**Baseline:** main `87623b22b7ab82de527365276c293b85f11d44c1`, 2026-09-17.

This is the implementation sequence for the execution substrate. It supersedes
the September 7 plan's web-research-first and single-Kubernetes-Job assumptions.
Keep its compatible contracts and tests, but do not implement its historical
executor in parallel. Git validates the substrate first; existing webfetch workers
come next; research later consumes webfetch.

## 1. Prove Tekton before committing to it

Create a disposable integration fixture under `service/ai_jobs/integration/tekton/`.
Pin a supported Tekton Pipelines release and images in the fixture; record Kubernetes,
CNI, storage, CPU architecture and resource requests. Use only Pipelines, with
no Dashboard, Triggers, Chains, Redis or public ingress required.

- Run one fixed credential-free Task, then a small Git worker/publisher pipeline
  against a fixture repository. Use separate Tasks/pods and a disposable provider
  test identity; the worker must never receive its write credential.
- Prove per-Task network policy, read-only roots, no service-account tokens, and
  resource/deadline enforcement against the actual generated pods, including
  Tekton-injected init containers and sidecars. Verify how the pinned Tekton
  version represents the required materializer init container; do not assume
  arbitrary PodSpec fields are supported. If it requires a separate materializer
  Task instead, record that profile revision and validate credential-free transfer
  before adopting it.
- Demonstrate the proposed per-run PVC handoff, producer termination before
  finalization, separate publisher input, offline inspection and trusted collection.
  Treat acceptance of this limited storage exception as an explicit recorded
  architecture decision. Include volume reclaim and interrupted cleanup behavior.
- Exercise a synthetic two-worker webfetch path with real transfer/collection,
  then the existing Rust binaries against controlled responses. Preserve error
  envelopes for worker exit code `1`; distinguish unavailable exit code `2`.
- Measure at least 30 warm and 30 cold-image runs, reporting p50/p95, failures,
  pod scheduling/image pull/transfer/compute time and idle controller footprint.
  Include constrained capacity and representative target-node/storage conditions.
  The target is end-to-end webfetch p95 within its existing 25-second budget;
  report cold-start misses explicitly rather than removing them from the sample.
- Restart `ai-jobs` and Tekton during execution; cancel while pending and running;
  simulate an API create response lost after resource creation.

**Deliverable:** a decision record with measured results, accepted storage policy,
version pins and either “adopt Tekton” or a reasoned fixed-Job fallback. If a metric
fails, record the proposed remediation and repeat that measurement. Do not build
both production backends or weaken isolation to finish the spike.

## 2. Make run admission durable and recoverable

Work in `service/ai_jobs/src/ai_jobs/{runs,storage,contracts,executor}.py` and focused
new persistence modules. Add migrations in `service/ai_jobs/migrations/` and tests
in `service/ai_jobs/tests/`. Names are proposed; preserve existing module ownership
where it is clearer than introducing more layers.

- Implement Postgres-backed runs, execution attempts, append-only audit events,
  dispatch intents, artifact metadata and publication intents/receipts.
- Enforce idempotency uniqueness and digest conflict checks in the database.
  Commit admission and dispatch intent in one transaction; do not call Kubernetes
  inside that transaction. Dispatch retries must locate the same named execution.
- Add authenticated caller/tool authorization before admission, repository authority
  resolution, bounded active/queued run counts, and a whole-run deadline.
- Encapsulate legal state transitions, concurrent completion/cancellation handling,
  and immutable terminal outcomes. Track cleanup and cancellation intent separately.
- Replace `start(run_id, profile)` with the reviewed ensure/observe/cancel boundary.
  Deliver validated inputs explicitly. Keep full sensitive input in a volatile,
  bounded store initially; a restart before delivery fails predictably rather than
  replaying missing input or persisting a raw URL in the audit/outbox.
- Implement leader ownership and restart reconciliation without building a DAG
  scheduler. Backend status is observed; domain status and audit stay in Postgres.

**Acceptance:** transaction rollback, concurrent duplicate admission, same-key
payload conflict, lost create response, crash before/after dispatch, lost sensitive
input, stale watch and late result tests. No admitted request can become an
untracked duplicate execution. No invalid result can mark a run successful.

## 3. Implement the chosen backend and artifact lifecycle

For Tekton, add `executors/tekton.py`, reviewed profile definitions and runtime
fixtures. Build only the backend selected in step 1.

- Freeze profile version, image digests, task specification and policies per run.
  Use stable names and verify Kubernetes UID/spec digest when recovering creation.
- Implement bounded submit/observe/cancel and list-based reconciliation. The backend
  handles task ordering; `ai-jobs` owns capability admission and result validation.
- Render narrow service accounts/RBAC, network policies and resource limits. Worker
  pods cannot select runtime settings or read Kubernetes Secrets through the API.
- Implement the accepted artifact transport with run/attempt/stage binding, size
  limits, immutable consumer copies and bounded retention. Do not use whole-content
  Tekton Results, log scraping or termination messages as artifact transport.
- Keep keys separate from handoff storage; deliver no provider credentials in
  parameters, labels, artifacts or generic logs. Restrict grants to the exact stage.
- Add a janitor independent of pipeline `finally` tasks. Audit cleanup attempts and
  retry orphan deletion, including Secrets and underlying storage reclamation.

**Acceptance:** reject caller-supplied images/commands/mounts; reject cross-run
artifacts and malformed manifests; prove a worker cannot alter publisher input
or reach the Kubernetes API. Cancel and expire runs without success resurrection.
Deletion/reconciliation tests demonstrate no indefinitely orphaned run resources.

## 4. Ship Git change as the first usable profile

Extend `tools/git_change.py`, add provider/materializer/publisher modules and the
fixed profile. Keep the public `{repository_id, instruction}` contract and implement
its currently missing result validator with shared fixtures.

- Resolve an opaque ID to an authorized Git repository and pinned base commit.
  Implement GitHub first, behind a provider-neutral interface. Test a non-GitHub
  repository resolver without requiring a GitHub installation.
- Implement short-lived read-only materialization, credential cleanup, and sandboxed
  edits/checks with only the profile's required tools. No write credential reaches
  repository-controlled code. Preserve out-of-band auth when retaining Git history.
- Collect and finalize bounded changes and check evidence after the worker stops.
  Publisher runs in a separate pod with a different input copy and fresh checkout;
  applying changes must not execute repository hooks, filters or other code.
- Revalidate authority and any required approval before credential issuance and
  external mutation. Bind approval to the exact artifact/base/destination when
  exact-content approval is required; a pipeline Boolean cannot establish it.
- Record publication intent before mutation. Reconcile existing branch/commit/PR
  after an ambiguous provider response instead of creating duplicates. Token expiry,
  revoked authority, changed base and concurrent cancellation have explicit outcomes.
- Return typed results including repository ID, base/head, check summary and PR
  reference; record the actual provider actor. Never merge or write the base branch.

**Acceptance:** one authorized test invocation produces one reviewable PR. Duplicate
submission, a crash after push/PR creation, revoked authority and a late cancellation
do not silently create a second PR or hide a completed side effect. Live inspection
shows no shared worker/publisher volume or credentials. Remove test resources.

## 5. Connect the existing webfetch workers

Use `service/web_fetch/` and `tools/web_fetch.py`; do not rewrite conversion/scanning.
Update `docs/runbooks/webfetch.md` to match the implemented transport, not the spike.

- Package and pin both Rust binaries and the rule bundle. Fetch/inspection remain
  separate Tasks; inspection denies all egress. Include the cluster destination policy.
- Wire sealed handoff, separate per-run key, read-only finalized inspection input,
  and bounded output collection. Implement explicit executor-owned cleanup for
  read-only input rather than letting the inspector silently fail to remove it.
- Translate exit codes into recorded stage outcomes so a fetch failure still yields
  a validated error envelope. A successful collector does not imply accepted content.
- Apply the whole-run deadline to scheduling, all helper Tasks, transfer and cleanup
  initiation. Reject partial/invalid envelopes and retain external provenance.
- Measure the real end-to-end 25-second budget using the same protocol as step 1.
  If unmet, keep the capability unadvertised until a reviewed design change resolves it.

**Acceptance:** existing contract fixtures plus real-pod success, rejection, malformed
handoff, read-only input, provider failure, image-pull delay, cancellation, timeout
and collector failure cases. The inspector has no network access; raw bodies and
keys disappear according to verified retention/cleanup, not merely a successful Task.

## 6. Package HearthAI and integrate the chat boundary

Update `deploy/charts/hearthai/`, its schema/tests, `deploy/README.md`, examples and
`service/ai_jobs/openapi.json` together. Preserve bundled default-on simple Postgres
and external Postgres support; use a separate run database/role. No bundled CNPG.

- Package the service, migrations, profiles, RBAC, network policy, janitor and images.
  Document home-ops' Tekton CRD/controller prerequisite and compatibility pins.
  Do not silently install cluster-scoped controllers from an application release.
- Add authenticated tool transport and the thin Open WebUI adapter. Register only
  capabilities whose persistence, profiles, policy and backend pass readiness.
- Validate the actual Open WebUI timeout/reconnect behavior. Webfetch retains its
  synchronous content-or-error contract. Git work may exceed chat HTTP timeouts:
  define durable session delivery and authenticated cancellation before advertising
  long-running Git support; internal run IDs do not imply a generic model-facing API.
- Add metrics, redacted diagnostics and runbooks for backend outage, reconciliation,
  uncertain publication, retention and schema/profile upgrades. Preserve in-flight
  versioned profile definitions until their runs and audit capture finish.
- Roll out disabled by default to a test namespace, then enable only Git; enable
  webfetch after its acceptance gate. Rollback disables admission, drains/cancels
  active work and retains compatible reconciliation/cleanup until resources settle.
  Never delete live CRDs as an application rollback shortcut.

**Acceptance:** Helm render/schema validation, existing deployment tests, fresh and
upgrade migrations, external/bundled DB variants, readiness without Tekton, and one
chat-to-PR plus chat-to-webfetch integration. No direct local-execution fallback.
Backups restore run/audit records; lost ephemeral input has the documented outcome.

## 7. Add higher-level workflows only when needed

After the substrate works, implement bounded research as a consumer of admitted
`web_fetch` child runs with parent grants, aggregate budgets, deadlines and cancellation.
Keep dynamic model decisions inside the bounded research worker; do not generate
Tekton YAML from model output or represent every reasoning turn as a new pipeline.

Evaluate n8n for one named recurring automation. Start with an HTTP capability call
using a scoped automation identity and stable invocation key, plus a tested delivery
and approval path. No repository/cluster credentials in n8n for HearthAI operations.
Do not adopt queue mode/Redis or new public asynchronous APIs without an actual need.
If a single trusted CronJob calling the same API is enough, prefer that simpler flow.

**Acceptance:** the chosen automation reuses the same authorization/audit path as
chat, duplicate delivery is safe, and any human wait releases compute resources.

## Completion and review evidence

The substrate is complete only when steps 1–6 are demonstrated on the target class
of cluster and documented. Step 7 is follow-on scope. Each implementation PR should
include the changed responsibility, relevant failure-mode evidence and the commands
used to verify it. Do not claim the executor exists because contracts or manifests
render successfully. Keep the decision record and roadmap status current.
