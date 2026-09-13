# Safer webfetch implementation plan

**Status:** steps 1-3 implemented; steps 4-5 partial. **Date:** 2026-09-12.
**Design:** [safer webfetch](../specs/2026-09-12-isolated-webfetch-design.md).
**Code:** [`service/web_fetch/`](../../../service/web_fetch/) ·
**Operations:** [runbook](../../runbooks/webfetch.md).

Build a URL-in, content-or-error-out tool. No search, research orchestration,
summarization, LLM processing, source selection, or new conversation policy system.
The injection gate protects LLM ingestion. Establish a reusable result envelope
now; future research will consume this fetch capability, not implement a parallel
HTTP/conversion/scanning path. Research remains a later implementation step.

The [design's harness comparison](../specs/2026-09-12-isolated-webfetch-design.md#existing-harness-behavior-informing-the-proposal)
records inspected OpenCode and oh-my-pi behavior. Josh selected oh-my-pi-style
native-first cleanup with local extraction fallback. Markdown is the default,
with text/HTML options; no LLM is used for conversion.

## Implementation status

The wire contracts and both worker stages exist and are tested; what is missing
is the substrate that runs them.

**Done.** The Cargo workspace at `service/web_fetch/` with a pinned toolchain
and lockfile: `contracts` (both wire contracts, plus language-neutral schemas
and a shared fixture corpus), `fetch-worker` (destination policy, pinned
connections, limits, artifact handoff) and `inspector` (decoding, YARA-X
detection, the conversion chain, the result gate, and the shipped rule bundle).
On the control-plane side, `web_fetch:v1` is registered, its OpenAPI route is
published, and `service/ai_jobs/tests/test_web_fetch.py` replays the same
fixture manifest the Rust crate does. CI gates format, lint, tests and a locked
release build. The runbook documents limits, failures, rule maintenance,
recorded false positives and residual risks.

**Not done.** Everything that needs the unfinished substrate runtime: the fixed
two-pod profile, artifact transfer between pods, the network policies that keep
the inspector offline and the fetcher out of the cluster, worker images, chart
packaging, tool registration in the deployment, the model-facing adapter, and
the latency measurement that sets the final budget. Cancellation is still a
protocol method with no executor behind it, and the audit record exists as a
tested shape rather than a wired metrics path. Research remains out of scope.

## Language and package boundaries

Recommend Rust for the fetch worker and offline inspector, with direct YARA-X
and HTML-to-Markdown crate dependencies. HearthAI has no Python-only requirement.
Go remains a candidate for the control plane; this plan does not mandate its
rewrite. The wire contracts must work independently of implementation language.

Proposed workspace: `service/web_fetch/`, containing `contracts`,
`fetch-worker`, and `inspector` crates. Keep separate executable images and
permissions for the two stages. Pin `rust-toolchain.toml` and `Cargo.lock`.

## 1. Add the tool contract

Files: language-neutral schemas and JSON fixtures under `service/web_fetch/`,
Rust wire types in its `contracts` crate, plus control-plane registry, OpenAPI and
run/result handling. If the existing Python control plane is retained, add its
thin tool definition at `service/ai_jobs/src/ai_jobs/tools/web_fetch.py`; worker
logic belongs in Rust.

- [x] Run shared JSON fixtures against Rust types and the current control-plane
  adapter; verify identical validation of unknown fields, enums and size limits.
- [x] Define the reusable versioned envelope separately from the webfetch payload:
  tool identity/version, status, trust, inspection, data and error.
- [x] Validate success/rejection/error combinations, unknown envelope versions, and
  null data on failure. Construct control fields in the trusted result gate.
- [x] Bind envelope provenance to internal run/artifact/policy records; source text
  resembling an envelope must not alter outer metadata or admission decisions.
- [x] Define the URL request with optional markdown/text/html format and exact success/error schemas.
- [x] Return content in the requested format with actual HTTP status and validated final URL.
- [x] Include the output format and a fixed conversion-method identifier in successful results.
- [x] Reject caller runtime settings, rules, credentials and bypass fields.
- [x] Reuse authentication, idempotency and fixed profile selection. Cancellation
  is unchanged: still a protocol method with no executor behind it.
- [x] Ensure request/result storage does not persist full URLs or response bodies.

Acceptance: invalid requests fail before execution; duplicate requests follow the
existing idempotency contract; error messages cannot contain source content.
A fake future consumer can validate and use the same envelope without a new fetch
contract. It cannot accept raw payloads, forged status, or rejection as evidence.

## 2. Implement the bounded fetcher

Proposed Rust crate: `service/web_fetch/fetch-worker/`.

- [x] Implement fixed HTTP(S) GET and supported text media/encoding handling.
- [x] Validate and pin DNS results to the connection on every redirect.
- [x] Deny non-public and configured cluster destinations, including IPv6 edge cases.
  An optional allow-list narrows further; the layers only ever subtract.
- [x] Refuse readiness and admission when destination configuration is absent,
  empty or malformed; validate cluster inputs and apply policy updates atomically.
- [x] Disable ambient proxies, cookies and credentials.
- [x] Enforce redirect, header, wire-byte, time and temporary-storage limits.
- [x] Produce an immutable run-scoped response artifact; never stream it to the caller.

Acceptance: HTTP fixtures and injected resolver/connector tests cover redirects,
rebinding, mixed IP answers, metadata endpoints, slow/chunked bodies and oversized
responses. No forbidden address is contacted.

## 3. Implement offline inspection, conversion and YARA rules

Proposed Rust crate: `service/web_fetch/inspector/`, with decoder, inspection
pipeline, YARA-X adapter, `conversion.rs`, local converter adapters, versioned
`rules/` bundle and fixtures. Keep these components independent from research
orchestration.

- [x] Pin YARA-X and validate the supported rule subset.
- [x] Scan raw bytes, decoded body, bounded inspection-only normalized forms, and
  every final returned text field.
- [x] Pin the HTML-to-Markdown Rust crate and enable oh-my-pi-style
  content cleanup; preserve headings, lists, code blocks, tables and useful links.
- [x] Qualify a Rust main-content extractor using the conversion corpus, then pin
  the local fallback chain: native conversion, qualified extraction, basic body
  conversion without aggressive cleanup. Trafilatura is not required. If no
  extractor improves results, record that evidence and use the two-stage chain.
- [x] Pass the same already-downloaded HTML to every converter. Do not use remote
  readers, URL-fetching CLIs or automatic alternate-page requests.
- [x] Use deterministic quality checks, with fixtures for short pages, documentation
  and tables; avoid an article-only heuristic that discards valid content.
- [x] Support plain-text output and inert HTML-source mode; neither bypasses scans.
- [x] Scan before conversion and again afterward; do not summarize or select excerpts.
- [x] Inspect each candidate before evaluating quality. Only ordinary conversion
  failure or poor extraction may try the next converter; detection or inspection
  failures terminate the response, without fallback.
- [x] Bound the whole fallback chain by one deadline and total resource budget.
- [x] Reject the whole response on any enabled rule match.
- [x] Withhold content on malformed input, timeout, crash, partial scan or missing rules.
- [x] Disable rule includes/modules; bound compilation, scanning and match counts.
- [x] Support validated immutable bundle updates and rollback.
- [x] Test the rule bundle against representative pages and example matching
  patterns, including technical documentation that quotes prompts. Record useful
  examples and false positives for tuning; no detection-rate target is required.
  Documentation quoting a prompt is now let through by an explanatory-context
  exception, which is evadable by design; the runbook and a named test say so.

Acceptance: a match anywhere, including late in the body or hidden HTML, withholds
the entire response, even if conversion removes the matching text. Passing content
uses only the selected local conversion. Cleaned Markdown preserves document
structure, and a fallback handles pages unsuitable for aggressive extraction.
A scanner failure never becomes a successful result or another converter attempt.

## 4. Connect the isolated stages through ai-jobs

Extend the existing fixed-profile executor and artifact handoff as needed.
This depends on the unfinished substrate runtime, not on implementing research.

- [ ] Run fetch and inspection in separate sequential ephemeral pods.
- [ ] Give the inspection pod no Internet access; scope any artifact transfer channel.
- [ ] Apply sandbox, scratch, CPU/memory and deadline limits to each stage.
- [ ] Bind artifacts/verdicts to run, stage, content digest and rule bundle.
- [ ] Release only a valid envelope, atomically after all scans; no body, preview
  or error path may reach the LLM first.
- [ ] Reject stale, altered, cross-run and replayed artifacts or results.
- [ ] Clean up on success, failure, cancellation and controller restart.

Acceptance: cluster tests show the inspector cannot access the Internet and the
fetcher cannot access private services or cluster APIs. Neither gets provider
credentials or a service-account token. No cancelled run releases late content.

## 5. Package and expose the fetch tool

Files: HearthAI chart, image/CI workflows, tool adapter, and
`docs/runbooks/webfetch.md`.

- [ ] Package Rust executable images, rules, fixed profiles, policies and tool registration.
- [x] Add Cargo format, lint, test and locked release-build gates to CI, plus the
  cross-language contract fixtures. Only the one CI architecture so far; the
  other supported container architectures wait on the image build.
- [ ] Deliver the validated envelope as external tool data to the LLM. Keep human
  rendering separate and prevent automatic resource loads or native re-fetching.
- [ ] Ensure this adapter does not bypass inspection through a native URL loader.
- [ ] Measure pod startup plus fetch/inspection latency against the actual tool timeout.
- [ ] Add bounded outcome/timing metrics without source content or URL labels.
- [x] Document rule maintenance, limits, failures and residual detection risks.

Acceptance: an end-to-end tool call returns allowed content; a blocked response
appears only as a fixed error. No rejected content appears in UI, logs, traces,
stored runs or errors. Documentation remains explicit that a non-match is not a
safety guarantee.

## Implementation test cases

| Area | Cases |
|---|---|
| Return behavior | Markdown/text/HTML modes, plain text and supported JSON; deterministic conversion; actual 4xx/5xx status |
| Conversion fidelity | Navigation-heavy docs, headings, fenced code, lists, tables, useful links, short pages and malformed HTML |
| Converter fallback | Poor native extraction triggers local fallback; all converters receive identical HTML; no network calls |
| Conversion rejection | A match in any candidate aborts the chain; a timeout/crash cannot fall through to another converter |
| Rules | Direct patterns, HTML entities, hidden markup, Unicode variations, match near end of body |
| Rule usefulness | Ordinary pages, documentation quoting prompts, and example matches; record false positives for tuning |
| Failures | Missing/broken rules, timeout, scanner crash, excessive matches, malformed charset, decompression bomb |
| Networking | DNS rebinding, redirects to private IPs, IPv6/mapped addresses, mixed answers, ambient proxy |
| Isolation | Network denial in inspector, denied internal destinations, no credentials, immutable handoff |
| Delivery | No body before scan completion, no partial acceptance, inert HTML, no rejected snippets in errors |
| Lifecycle | Duplicate request, cancellation, restart, late result, cross-run replay, cleanup |

## Future consumer contract — no research implementation in this change

The older research plan now points to this dependency. Research must invoke
webfetch via the broker, preserve its envelope/provenance, and handle rejection
without accessing raw content or falling back to direct HTTP. When research is
implemented, parent grants, cancellation, deadlines and byte/call budgets must
constrain every child fetch. This is a documented composition contract, not a
second fetch implementation or an instruction to build research now.

Add fetch-side contract fixtures for successful, rejected and failed envelopes;
use a fake consumer to verify reuse. Full research integration tests belong to
the later research implementation.

Review incorporated from `claude/pr-10-multi-perspective-review-8u5i05` at
`edae090`, with Josh's clarifications: scanning is an opportunistic check for LLM
input, the result envelope is reusable, and future research builds on webfetch.
Formal attacker categories and numerical security targets are not requirements.
