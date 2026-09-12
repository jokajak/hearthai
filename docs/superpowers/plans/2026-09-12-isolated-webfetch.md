# Safer webfetch implementation plan

**Status:** planning only. **Date:** 2026-09-12.
**Design:** [safer webfetch](../specs/2026-09-12-isolated-webfetch-design.md).

Build a URL-in, content-or-error-out tool. No search, research orchestration,
summarization, LLM processing, source selection, or new conversation policy system.

The [design's harness comparison](../specs/2026-09-12-isolated-webfetch-design.md#existing-harness-behavior-informing-the-proposal)
records inspected OpenCode and oh-my-pi behavior. Josh selected oh-my-pi-style
native-first cleanup with local extraction fallback. Markdown is the default,
with text/HTML options; no LLM is used for conversion. This is a documentation-only
implementation plan, not a request to start runtime changes in this PR.

## 1. Add the tool contract

Files: `service/ai_jobs/src/ai_jobs/tools/web_fetch.py`, registry, OpenAPI,
run/result handling, and contract fixtures.

- [ ] Define the URL request with optional markdown/text/html format and exact success/error schemas.
- [ ] Return content in the requested format with actual HTTP status and validated final URL.
- [ ] Include the output format and a fixed conversion-method identifier in successful results.
- [ ] Reject caller runtime settings, rules, credentials and bypass fields.
- [ ] Reuse authentication, idempotency, cancellation and fixed profile selection.
- [ ] Ensure request/result storage does not persist full URLs or response bodies.

Acceptance: invalid requests fail before execution; duplicate requests follow the
existing idempotency contract; error messages cannot contain source content.

## 2. Implement the bounded fetcher

Proposed package: `service/web_fetch_worker/`.

- [ ] Implement fixed HTTP(S) GET and supported text media/encoding handling.
- [ ] Validate and pin DNS results to the connection on every redirect.
- [ ] Deny non-public and configured cluster destinations, including IPv6 edge cases.
- [ ] Disable ambient proxies, cookies and credentials.
- [ ] Enforce redirect, header, wire-byte, time and temporary-storage limits.
- [ ] Produce an immutable run-scoped response artifact; never stream it to the caller.

Acceptance: HTTP fixtures and injected resolver/connector tests cover redirects,
rebinding, mixed IP answers, metadata endpoints, slow/chunked bodies and oversized
responses. No forbidden address is contacted.

## 3. Implement offline inspection, conversion and YARA rules

Proposed package: `service/web_fetch_inspector/`, with decoder, inspection pipeline,
YARA-X adapter, `conversion.py`, local converter adapters, versioned `rules/` bundle
and fixtures. Keep these components independent from research orchestration.

- [ ] Pin YARA-X and validate the supported rule subset.
- [ ] Scan raw bytes, decoded body, bounded inspection-only normalized forms, and
  every final returned text field.
- [ ] Pin the Rust HTML-to-Markdown engine's Python binding and enable oh-my-pi-style
  content cleanup; preserve headings, lists, code blocks, tables and useful links.
- [ ] Add a fixed local fallback chain: native conversion, Trafilatura extraction,
  then basic body conversion without aggressive cleanup.
- [ ] Pass the same already-downloaded HTML to every converter. Do not use remote
  readers, URL-fetching CLIs or automatic alternate-page requests.
- [ ] Use deterministic quality checks, with fixtures for short pages, documentation
  and tables; avoid an article-only heuristic that discards valid content.
- [ ] Support plain-text output and inert HTML-source mode; neither bypasses scans.
- [ ] Scan before conversion and again afterward; do not summarize or select excerpts.
- [ ] Inspect each candidate before evaluating quality. Only ordinary conversion
  failure or poor extraction may try the next converter; detection or inspection
  failures terminate the response, without fallback.
- [ ] Bound the whole fallback chain by one deadline and total resource budget.
- [ ] Reject the whole response on any enabled rule match.
- [ ] Withhold content on malformed input, timeout, crash, partial scan or missing rules.
- [ ] Disable rule includes/modules; bound compilation, scanning and match counts.
- [ ] Support validated immutable bundle updates and rollback.

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
- [ ] Reject stale, altered, cross-run and replayed artifacts or results.
- [ ] Clean up on success, failure, cancellation and controller restart.

Acceptance: cluster tests show the inspector cannot access the Internet and the
fetcher cannot access private services or cluster APIs. Neither gets provider
credentials or a service-account token. No cancelled run releases late content.

## 5. Package and expose the fetch tool

Files: HearthAI chart, image/CI workflows, tool adapter, and
`docs/runbooks/webfetch.md`.

- [ ] Package images, rules, fixed profiles, policies and tool registration.
- [ ] Return the body as inert text with no HTML rendering or automatic resource loads.
- [ ] Ensure this adapter does not bypass inspection through a native URL loader.
- [ ] Measure pod startup plus fetch/inspection latency against the actual tool timeout.
- [ ] Add bounded outcome/timing metrics without source content or URL labels.
- [ ] Document rule maintenance, limits, failures and residual detection risks.

Acceptance: an end-to-end tool call returns allowed content; a blocked response
appears only as a fixed error. No rejected content appears in UI, logs, traces,
stored runs or errors. Documentation remains explicit that a non-match is not a
safety guarantee.

## Required test cases

| Area | Cases |
|---|---|
| Return behavior | Markdown/text/HTML modes, plain text and supported JSON; deterministic conversion; actual 4xx/5xx status |
| Conversion fidelity | Navigation-heavy docs, headings, fenced code, lists, tables, useful links, short pages and malformed HTML |
| Converter fallback | Poor native extraction triggers local fallback; all converters receive identical HTML; no network calls |
| Conversion rejection | A match in any candidate aborts the chain; a timeout/crash cannot fall through to another converter |
| Rules | Direct patterns, HTML entities, hidden markup, Unicode variations, match near end of body |
| False positives and misses | Legitimate quoted attacks; paraphrased/multilingual/encoded attacks; record actual outcomes |
| Failures | Missing/broken rules, timeout, scanner crash, excessive matches, malformed charset, decompression bomb |
| Networking | DNS rebinding, redirects to private IPs, IPv6/mapped addresses, mixed answers, ambient proxy |
| Isolation | Network denial in inspector, denied internal destinations, no credentials, immutable handoff |
| Delivery | No body before scan completion, no partial acceptance, inert HTML, no rejected snippets in errors |
| Lifecycle | Duplicate request, cancellation, restart, late result, cross-run replay, cleanup |

The existing web-research implementation plan is unchanged. Reuse of this fetch
tool by another capability can be planned separately.
