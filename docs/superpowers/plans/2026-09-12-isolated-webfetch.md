# Isolated webfetch implementation plan

**Status:** proposed, not implemented. **Date:** 2026-09-12.
**Design:** [isolated webfetch](../specs/2026-09-12-isolated-webfetch-design.md).

Deliver a model-facing `web_fetch:v1` and one shared response-admission boundary
for research. All enabled detector matches reject the complete response; scanning
failures release no content. Accepted evidence retains external provenance.

## Sequence and dependencies

Git change work remains the first substrate proof. Reuse the generic admission,
authentication, durable lifecycle, stage scheduling, and fixed-profile executor;
do not build a second job runner. The existing [research plan](2026-09-07-ai-jobs-web-research.md)
supplies those dependencies. Its Tasks 5–6 must adopt this pipeline and its Tasks
7–10 must adopt the stage, packaging, and release gates below.

Tasks 1–3 can establish contracts and offline behavior before the substrate is
deployable. Integration and release depend on actual ephemeral execution,
run-scoped credentials, cancellation/recovery, and enforceable network isolation.
Each implementation PR must include the evidence for its acceptance gate.

### 1. Freeze integration and contracts

Proposed changes:
- `service/ai_jobs/src/ai_jobs/tools/web_fetch.py`
- `service/ai_jobs/src/ai_jobs/contracts.py`, `registry.py`, `runs.py`, `storage.py`
- `service/ai_jobs/openapi.json`
- contract tests and shared JSON fixtures

Checklist:
- [ ] Add exact request/result schemas, fixed failures, typed three-state verdicts,
  immutable artifact references, and non-forgeable internal provenance.
- [ ] Specify tool version independently from effective inspection-policy digest.
- [ ] Register only the fixed webfetch profile; reject caller runtime/policy fields.
- [ ] Make persistent run storage metadata-only for sensitive fetch/research payloads;
  define transient request/result lifecycle and restart behavior.
- [ ] Spike the pinned Open WebUI tool timeout, bypass settings, inert rendering,
  and server-side write/read authorization hooks.
- [ ] Measure cold starts and allocate nested deadlines within existing research
  budgets; revise the integration envelope explicitly if it cannot fit.

Acceptance: malformed requests, forged trust/verdict fields, unknown tool versions,
cross-run handles, and same-idempotency-key/different-request reuse fail before
execution or content delivery. WebUI gaps are recorded as release blockers.

### 2. Implement offline inspection and YARA policy

Proposed package: `service/web_content_processor/`, with `normalize.py`,
`extract.py`, `detectors.py`, `yara_detector.py`, `pipeline.py`, tests,
and `rules/` plus a manifest. Shared processor image and contract are consumed
by both webfetch and research; avoid duplicate filter implementations.

- [ ] Pin YARA-X; compile and validate the supported module-free, include-free subset.
- [ ] Implement immutable bundle manifests, atomic activation, and pinned run policy.
- [ ] Add bounded raw, decoded, normalized, extracted, and final-output scans.
- [ ] Reject the whole response for any active match and withhold on any ERROR.
- [ ] Add supervisor deadlines and memory limits for parsers, compilation, and scans.
- [ ] Establish a small documented rule pack and benign/malicious/evasion corpus.
- [ ] Reject unsupported formats and oversized content rather than scanning prefixes.

Acceptance: an injected canary in any required representation never reaches a
successful output; absent/broken policy, timeout, crash, too-many-matches, and
malformed verdicts cannot produce admission. Legitimate documentation false
positives are measured and reviewed before rule activation.

### 3. Implement constrained transport and immutable handoff

Proposed package: `service/web_fetch_worker/`; substrate adapters under
`service/ai_jobs/src/ai_jobs/` for artifact exchange and profile capabilities.

- [ ] Implement fixed HTTP(S) GET, redirect/address revalidation and connection pinning.
- [ ] Enforce bounded headers, compression expansion, body size and read deadlines.
- [ ] Deny private/reserved/cluster destinations and ambient proxy/auth/cookie behavior.
- [ ] Materialize run-bound immutable snapshots and authenticate every stage transition.
- [ ] Ensure response bytes appear only in ephemeral quarantine until final admission.
- [ ] Account for denied/retried fetches and every representation against run budgets.

Acceptance: fake resolver/connector tests cover DNS rebinding, mixed A/AAAA,
IPv4-mapped IPv6, redirect-to-private, ports/userinfo, and encoded hosts. A local
HTTP fixture server covers chunking, compression bombs, unsupported charset,
malformed headers, slow bodies, redirects and cancellation. Altered snapshots or
cross-run replay are rejected.

### 4. Add fixed multi-stage execution

Extend the substrate's executor/profile abstraction rather than exposing stages
or Kubernetes configuration as model request parameters.

- [ ] Run fetch and offline inspection in separate ephemeral pods.
- [ ] Apply least-privilege per-stage channels, no service-account token, read-only
  roots, scratch/resource/deadline ceilings, and reviewed egress policy.
- [ ] Validate authenticated verdicts and artifact/policy binding in the result gate.
- [ ] Implement cleanup and terminal-state handling across controller restart,
  orphaned pods, late results, failed scheduling, cancellation, and expiry.
- [ ] Ensure parser/scanner stdout/stderr never becomes generic model-visible output.

Acceptance: cluster tests demonstrate denied LAN/metadata/cluster API/direct
Internet access outside the allowed path; the processor cannot make outbound
requests. No worker can acquire provider secrets or access another run. Cancellation
and crashes leave no reusable raw artifacts or delayed content delivery.

### 5. Enforce provenance and action authority before model exposure

Proposed additions: session provenance and authorization gate in the HearthAI
chat integration; identity-bound hooks at all governed action endpoints.

- [ ] Keep privileged planning separate from answer-only evidence consumption.
- [ ] Set conservative session taint atomically before evidence delivery.
- [ ] Carry provenance through summaries, topic carry-over, caches and memory records.
- [ ] Require exact human authorization for web-influenced writes and new
  content-derived outbound destinations/payloads.
- [ ] Bind approvals to action digest, principal, destination, scope, and expiry.
- [ ] Prevent native Open WebUI and alternate tool pathways from bypassing the gate.

Acceptance: a deliberately undetected paraphrased injection may reach the answerer
but cannot silently write memory, launch a Git publisher, fetch an exfiltration
URL, or transmit a secret through search. Forged JSON trust labels and “approved”
source text do not change grants. Approval of one write never clears taint.

### 6. Integrate bounded research and tool delivery

Modify `web_research.py` contracts as needed, the future broker, and
`service/web_research_worker/`; reuse the exact processor and admission contract.

- [ ] Route provider snippets/metadata and every fetched source through admission.
- [ ] Accumulate only independently admitted evidence; never salvage rejected text.
- [ ] Validate broker-recorded citations, exact final output, and provenance.
- [ ] Preserve fixed failure messages and limitations without rejected bytes.
- [ ] Use deterministic extraction for MVP; defer optional tool-less LLM extraction.
- [ ] Cap repeated rejected requests; do not retry a detector hit under another policy.

Acceptance: a clean source plus a rejected source produces evidence only from the
clean source and a fixed limitation. All sources rejected produces an honest
failure. No rejected text survives in summaries, citations, errors, UI, or traces.

### 7. Package, evaluate and release

Changes belong in `deploy/charts/hearthai/`, CI workflows, image release
automation, and a new `docs/runbooks/webfetch.md`. Update architecture/status docs
only when behavior has actually shipped.

- [ ] Package fixed profiles, processor image, pinned rules, app policy/RBAC and
  tool registration; expose only operator configuration.
- [ ] Add rule digest visibility, offline promotion checks, rollback instructions,
  and readiness behavior when no valid bundle is active.
- [ ] Validate home-ops supplied cluster ranges and supported network enforcement.
- [ ] Add metrics for verdicts, latency, cleanup, resource ceilings and rule versions,
  with no content/URL/query labels or raw request persistence.
- [ ] Test installed images end to end through the pinned Open WebUI integration.
- [ ] Record corpus results, false-positive/false-negative counts, p95 latency,
  peak memory, and multi-pod cold-start behavior before enabling the tool.

Release gate: all isolation and authority tests pass, every known active-rule
match is withheld, incomplete inspections fail closed, and no alternate chat
path bypasses the boundary. Demonstrate residual detection misses without
authority escalation. Accuracy targets for heuristic rules are set from the
measured corpus; do not invent a security percentage.

## Minimum adversarial matrix

| Area | Cases and required assertion |
|---|---|
| Direct patterns | Role spoofing, tool impersonation, exfiltration templates; active matches withhold whole response |
| Representations | Entities, comments, hidden HTML, Unicode/default-ignorables, split tokens, late-body payloads; scan all required views before projection |
| Detection limits | Paraphrase, multilingual, encoded payloads; record misses and independently prove action restrictions |
| False positives | Security articles, quoted attacks, ordinary documentation; document actual rule behavior |
| Resource failures | Slow regex, too many matches, parser crash, decompression bomb, missing bundle; no content release |
| Transport | Redirect/DNS rebinding, mixed IP answers, metadata, unusual hosts; no forbidden connection |
| Provenance | Forged labels, summaries, carry-over, memory retrieval, changed approvals; no trust promotion |
| Leaks | Error, title, source URL, logging, tracing, Markdown image, native URL loader; no rejected bytes or automatic outbound fetch |
| Lifecycle | Duplicate delivery, late verdict, cancellation, restart, artifact mutation/replay; no stale or cross-run release |

## Explicit deferrals

General browsing, JavaScript, authenticated pages, PDFs/OCR/archives, recursive
decoding, dynamic worker/tool selection, inline user-defined rules, online
warn-only detectors, content caches, and a raw quarantine viewer are excluded
from v1. Semantic detectors and quarantined LLM extraction are later additions
behind the same fail-closed interface. Value-level provenance can refine the
conservative session gate after the simpler boundary is demonstrated.
