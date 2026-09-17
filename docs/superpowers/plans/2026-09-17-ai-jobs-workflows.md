# AI jobs workflow implementation plan

**Status:** proposed; documentation only, no runtime deployment authorized by this plan.
**Design:** [AI jobs architecture and engine decision](../specs/2026-09-17-ai-jobs-workflows-design.md)
**Baseline:** main `87623b22b7ab82de527365276c293b85f11d44c1`, 2026-09-17.

This is the implementation sequence for the execution substrate. It supersedes
the September 7 plan's web-research-first and single-Kubernetes-Job assumptions.
Keep its compatible contracts and tests, but do not implement its historical
executor in parallel. Git validates the substrate first; existing webfetch workers
come next; research later consumes webfetch.

## 1. Establish the Tekton integration

Create an integration fixture under `service/ai_jobs/integration/tekton/`. Pin a
supported Tekton Pipelines release and compatible worker images. Use only Pipelines;
no Dashboard, Triggers, Chains, Redis or public ingress is required.

- Run a fixed credential-free Task, then a Git worker/publisher pipeline against
  a fixture repository. Use separate Tasks/pods and a disposable provider identity;
  the worker must never receive its write credential.
- Verify network policy, read-only roots, no worker service-account tokens and
  resource limits on generated pods, including Tekton-injected containers.
- Verify how the pinned Tekton version represents the materializer init container.
  If a separate materializer Task is necessary, record the profile revision and
  validate credential-free transfer.
- Implement the per-run artifact handoff with producer termination, separate
  publisher input, offline inspection and trusted collection. Record the limited
  storage exception and normal cleanup behavior.
- Exercise existing Rust webfetch binaries against controlled responses. Preserve
  error envelopes for exit code `1`; distinguish infrastructure exit code `2`.

**Acceptance:** the fixed profiles execute and preserve their isolation and result
contracts. No recovery tests, cold/warm benchmark campaign or latency adoption gate
is required. Tekton is the planned backend; revisit it only for a concrete problem.

## 2. Implement admission, persistence and input delivery

Work in `service/ai_jobs/src/ai_jobs/{runs,storage,contracts,executor}.py` and focused
persistence modules. Add migrations and tests without introducing a recovery framework.

- Implement Postgres-backed run records, audit and results.
- Preserve idempotency uniqueness and digest conflict checks in the database.
- Authenticate and authorize callers and repositories before dispatch. Keep
  resource/concurrency limits separate from optional operational timeouts.
- Encapsulate legal transitions and terminal outcomes.
- Extend the executor boundary to submit, observe and cancel a fixed profile.
  Deliver validated inputs explicitly; do not attempt to reconstruct URLs from
  their stored digests or put sensitive input into pipeline metadata.
- Use one control-plane instance initially. Interrupted work or lost input may
  need operator inspection, manual cleanup and a fresh invocation.

**Acceptance:** unauthorized requests are refused; duplicate client requests do
not create another run; mismatched payloads conflict; validated inputs reach the
worker and invalid results cannot mark success. Automatic replay, transactional
outboxes, restart reconciliation and failover are deferred.

## 3. Implement the chosen backend and artifact lifecycle

For Tekton, add `executors/tekton.py`, reviewed profile definitions and runtime
fixtures. Build only the backend selected in step 1.

- Freeze profile version, image digests, task specification and policies per run.
  Use stable names and record Kubernetes identity for operator inspection.
- Implement submit/observe/cancel. The backend
  handles task ordering; `ai-jobs` owns capability admission and result validation.
- Render narrow service accounts/RBAC, network policies and resource limits. Worker
  pods cannot select runtime settings or read Kubernetes Secrets through the API.
- Implement the accepted artifact transport with run/attempt/stage binding, size
  limits, immutable consumer copies and bounded retention. Do not use whole-content
  Tekton Results, log scraping or termination messages as artifact transport.
- Keep keys separate from handoff storage; deliver no provider credentials in
  parameters, labels, artifacts or generic logs. Restrict grants to the exact stage.
- Implement normal completion/cancellation cleanup and document manual cleanup
  for interrupted runs. Automatic orphan reconciliation is deferred.

**Acceptance:** reject caller-supplied images/commands/mounts; reject cross-run
artifacts and malformed manifests; prove a worker cannot alter publisher input
or reach the Kubernetes API. Cancel and expire runs without success resurrection.
Verify normal cleanup removes temporary run resources.

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
- Record publication status. An ambiguous provider response requires operator
  inspection before retrying; automatic publication recovery is deferred. Token
  expiry, revoked authority and a changed base have explicit failure outcomes.
- Return typed results including repository ID, base/head, check summary and PR
  reference; record the actual provider actor. Never merge or write the base branch.

**Acceptance:** one authorized test invocation produces one reviewable PR. Duplicate
submission, revoked authority and a late cancellation
do not silently create a second PR or hide a completed side effect. Live inspection
shows no shared worker/publisher volume or credentials. Remove test resources.

## 5. Connect the existing webfetch workers

Use `service/web_fetch/` and `tools/web_fetch.py`; do not rewrite conversion/scanning.
Update `docs/runbooks/webfetch.md` to match the implemented transport.

- Package and pin both Rust binaries and the rule bundle. Fetch/inspection remain
  separate Tasks; inspection denies all egress. Include the cluster destination policy.
- Wire sealed handoff, separate per-run key, read-only finalized inspection input,
  and bounded output collection. Implement explicit executor-owned cleanup for
  read-only input rather than letting the inspector silently fail to remove it.
- Translate exit codes into recorded stage outcomes so a fetch failure still yields
  a validated error envelope. A successful collector does not imply accepted content.
- Reject partial/invalid envelopes and retain external provenance.
- Replace the fixed 25-second assumption with suitable configurable operational
  timeout behavior when wiring the executor. No pod time budget, benchmark or
  latency threshold is required to enable the capability.

**Acceptance:** existing contract fixtures plus real-pod success, rejection, malformed
handoff, read-only input, provider failure, cancellation
and collector failure cases. The inspector has no network access; raw bodies and
keys disappear according to verified retention/cleanup, not merely a successful Task.

## 6. Package HearthAI and integrate the chat boundary

Update `deploy/charts/hearthai/`, its schema/tests, `deploy/README.md`, examples and
`service/ai_jobs/openapi.json` together. Preserve bundled default-on simple Postgres
and external Postgres support; use a separate run database/role. No bundled CNPG.

- Package the service, migrations, profiles, RBAC, network policy, cleanup and images.
  Document home-ops' Tekton CRD/controller prerequisite and compatibility pins.
  Do not silently install cluster-scoped controllers from an application release.
- Add authenticated tool transport and the thin Open WebUI adapter. Register only
  capabilities whose persistence, profiles, policy and backend pass readiness.
- Make the adapter handle work that outlasts an HTTP connection without imposing
  that connection timeout on the pods. Choose a simple progress/result-delivery
  mechanism; no specific duration or crash-recovery guarantee is required initially.
  Preserve webfetch content-or-error semantics; internal run IDs do not imply a
  generic model-facing job API. This supersedes earlier synchronous-only timing
  assumptions where they would constrain pod runtime.
- Add metrics, redacted diagnostics and runbooks for backend outage, manual cleanup,
  uncertain publication, retention and schema/profile upgrades. Preserve in-flight
  versioned profile definitions until their runs and audit capture finish.
- Roll out disabled by default to a test namespace, then enable only Git; enable
  webfetch after its acceptance gate. Rollback disables admission, drains/cancels
  active work and retains compatible cleanup until resources settle.
  Never delete live CRDs as an application rollback shortcut.

**Acceptance:** Helm render/schema validation, existing deployment tests, fresh and
upgrade migrations, external/bundled DB variants, readiness without Tekton, and one
chat-to-PR plus chat-to-webfetch integration. No direct local-execution fallback.
Lost ephemeral input has the documented manual-retry outcome; recovery drills are deferred.

## 7. Add higher-level workflows only when needed

After the substrate works, implement bounded research as a consumer of admitted
`web_fetch` child runs with parent grants, aggregate resource/call budgets and cancellation.
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
