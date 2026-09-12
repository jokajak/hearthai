# Safer webfetch

**Status:** proposed design; no runtime implementation.
**Conversion decision:** oh-my-pi-style local conversion selected by Josh; recorded for implementation.
**Date:** 2026-09-12.
**Plan:** [implementation steps](../plans/2026-09-12-isolated-webfetch.md).

## Scope

Implement one tool: accept a URL, fetch its response in an isolated environment,
check it, and either return the fetched content or reject the entire response.

This is a fetch tool. It does not search, select sources, follow page links,
summarize, extract answers, or call an LLM. HTTP redirects are the only automatic
follow-up requests. Research implementation and new conversation-wide authorization
systems are outside this change. Webfetch establishes a reusable result envelope;
future web research must consume webfetch through that envelope. Build fetch first,
then research on top of it, with no parallel fetch/scan/conversion implementation.

The selected approach follows oh-my-pi's local conversion: clean HTML into readable
Markdown by default, with plain-text and HTML-source options and a bounded local
fallback chain. Conversion uses ordinary parser code, never an LLM. It removes page
boilerplate while preserving the document's useful content and structure.
HTML source is returned as inert text, not rendered or executed. If the complete
response or converted output exceeds the tool's limits, reject it; do not return
a scanned prefix.

**Any enabled detection rule that matches causes rejection of the whole response.**
A failed or incomplete inspection also returns no content. A successful inspection
means only that the configured rules did not match; the response remains untrusted.

## Why inspect responses

Scanning is an opportunistic extra check before fetched content enters the LLM's
context. This is a personal deployment; malicious content is not an expected
part of normal use. The tool is not making a comprehensive security claim.

The scanner protects the LLM's input, not the human reader. Choosing a URL or
having a person in the loop does not change which bytes are passed to the model.
A rule match withholds the response from model context; a non-match simply means
the configured checks did not flag it. The response remains external data.
Human-facing display and ordinary browser protections are separate concerns.

Use a small, useful rule bundle. Check representative pages and known example
patterns while implementing it, including technical documentation that quotes
prompts. Keep track of false positives so the tool remains useful. There is no
promised detection rate, assumed attacker population, or numerical security
benchmark required by this plan. If a rule causes unnecessary rejection, tune
the operator-managed bundle rather than letting the model bypass a match.

The existing sandbox and destination limits are ordinary boundaries for a
server-side fetcher. Missing or malformed required configuration leaves the
capability unavailable until it is configured; no elaborate threat model is
needed to justify that default.

## Implementation language

HearthAI is not constrained to Python. Choose languages per component based on
its libraries, runtime and maintenance needs; existing scaffolding does not
determine every future service's implementation.

**Recommendation: Rust for both webfetch stages.** Use YARA-X and the
HTML-to-Markdown engine directly as Rust crates. This avoids a language-binding
layer around the core inspection/conversion dependencies and lets the worker
images ship compiled executables without a Python interpreter. It does not imply
that all dependencies are statically linked or that Rust replaces sandboxing.

Go is a viable alternative, especially for the HTTP/Kubernetes-oriented
`ai-jobs` control plane. Keep that decision separate: the existing Python
control-plane scaffolding can invoke Rust workers through versioned JSON
contracts. No shared Python package or in-process language binding should be
required between those components. A control-plane rewrite is not a prerequisite
for this fetch tool, nor is retaining Python a permanent architecture constraint.

Use a Cargo workspace for the fetch worker, offline inspector and shared Rust
wire types. Publish language-neutral schemas and common JSON fixtures so the
current control plane and any future Go or Rust implementation can validate
identical requests, results, errors and stage messages. Pin the Rust toolchain
and dependency lockfile; test the target container architectures in CI.

## Execution

Use the existing `ai-jobs` admission and fixed-profile execution boundaries.
The model supplies the URL, never images, commands, rules, credentials, networking,
or pod configuration.

The fixed webfetch profile has two sequential stages:

1. **Fetch pod:** downloads one bounded HTTP(S) response into temporary storage.
   It has constrained public-web egress and no model, memory, provider credentials,
   host mounts, or Kubernetes API access.
2. **Inspection pod:** reads the completed immutable response, decodes, scans and converts it.
   It has no Internet access or tools. Only a completed successful inspection can
   release the response through the result gate.

Use separate pods so a parser or scanner exploit does not inherit the fetcher's
Internet access. Both stages run non-root with dropped capabilities, no service
account token, read-only roots, bounded temporary storage, CPU/memory limits, and
deadlines. Temporary artifact transfer, if networked, is the only narrowly scoped
inspection-stage network exception.

The substrate transfers a run-scoped immutable artifact after the fetcher exits.
Bind its digest and length to the run; reject changed artifacts, cross-run access,
late results, and replay. Do not share a writable volume with a live fetcher.
Neither stage gets general storage credentials. Remove temporary response data
after completion, failure, cancellation, or recovery from a controller restart.

The result gate verifies the stage identity, artifact binding, active rule bundle,
complete scan results, and output schema. No partial body is streamed to the
caller while fetching or scanning. Keep dependencies small and maintained.

## Tool contract

Proposed registration: `web_fetch:v1`.
Proposed endpoint: `POST /v1/tools/web-fetch`.

Request:

```json
{"url":"https://example.com/page","format":"markdown"}
```

Only `url` is required. `format` is an optional enum: `markdown` (default),
`text`, or `html`. It controls deterministic conversion, never inspection policy.
Reject unknown request fields. V1 uses GET with fixed headers and supports public
HTTP(S) text responses. No caller-supplied headers, cookies, credentials,
skip-scan option, or rules.

### Reusable result envelope

Webfetch is the first user of a versioned tool-result envelope. Define it now so
future consumers, including research, can carry the same status and provenance
without inventing another fetch response. Keep the common envelope small and
the tool's payload typed; this does not introduce a generic execution API.

The common fields are `envelope_version`, `tool`, `tool_version`, `status`,
`trust`, `inspection`, `data`, and `error`. `data` is validated against the named
tool/version's schema. A webfetch success looks like:

```json
{
  "envelope_version": 1,
  "tool": "web_fetch",
  "tool_version": 1,
  "status": "ok",
  "trust": "external_untrusted",
  "inspection": {"status": "no_match", "policy_id": "web-content-v1"},
  "data": {
    "source_id": "source-1",
    "final_url": "https://example.com/page",
    "http_status": 200,
    "content_type": "text/html",
    "retrieved_at": "2026-09-12T12:00:00Z",
    "format": "markdown",
    "conversion_method": "native",
    "content": "# Example\n\nFetched page text."
  },
  "error": null
}
```

The trusted result gate constructs the envelope after inspection. Source-provided
JSON resembling these fields remains text inside `data.content`; it cannot set
the real envelope's status, tool identity or trust. All remote strings in `data`,
including URLs, are inspected. Control fields use fixed enums or validated
server-owned identifiers. Internal records bind the result to its run, artifact
digest and immutable policy digest; `policy_id` is a reference to that policy,
not a source-authored explanation or a replacement for the internal binding.

Envelope v1 rejects unknown fields and unsupported versions. For `web_fetch:v1`,
`ok` requires typed
non-null `data`, null `error` and a completed `no_match` inspection. `rejected`
or `error` requires null `data` and a fixed error code/message. Inspection status
is one of `no_match`, `match`, `failed`, or `not_run`; only `no_match` permits
success. An absent policy uses a null policy ID and cannot produce success.
Source IDs are run-scoped opaque identifiers, not filesystem or download handles.

Consumers validate the envelope before admitting content to model context. A
future research caller must preserve provenance when using or transforming the
payload; it cannot flatten the response into unlabeled trusted instructions.
Envelope serialization is an enforceable integration contract, not a magic
prompt boundary: a model can still be influenced by admitted content. This plan
does not implement a new conversation-wide taint engine or authorize actions.

The trust marker describes fetched data; it grants no permissions. A 4xx/5xx HTTP
response may be returned with its actual status if its text body passes the same
checks. Transport failure and inspection failure are separate tool errors.

Every rejection/error has a fixed code and message, with no remote body, title,
headers, URL, matched text, or worker exception embedded in the error. For example:

```json
{
  "envelope_version": 1,
  "tool": "web_fetch",
  "tool_version": 1,
  "status": "rejected",
  "trust": "external_untrusted",
  "inspection": {"status": "match", "policy_id": "web-content-v1"},
  "data": null,
  "error": {"code": "content_rejected", "message": "Response content was withheld from model context."}
}
```

| Outcome | Code | Response body returned |
|---|---|---|
| Any enabled rule matches | content_rejected | None |
| Scan crashes, times out, is incomplete, or lacks valid rules | inspection_failed | None |
| Forbidden destination or redirect | unsafe_source | None |
| Unsupported media/charset or malformed encoding | unsupported_content | None |
| Body, header, or decoding limit exceeded | response_limit_exceeded | None |
| Network failure | fetch_failed | None |
| Whole-run deadline exceeded | deadline_exceeded | None |
| All required scans complete without matches | Successful result | Content in the requested format |

Reuse authenticated admission, caller-scoped idempotency, cancellation, and run
lifecycle. Do not persist raw URLs/bodies by blindly serializing the request or
result into the existing run store. Keep response delivery transient and durable
audit metadata bounded.

### Future research depends on webfetch

Deliver the inspected fetch and its envelope first. Future `web_research` uses
the typed webfetch capability through the `ai-jobs` broker, consumes only successful
envelopes, and handles rejection as unavailable evidence. It must not gain a direct
HTTP client, another converter/scanner, a raw-body route, or an alternate loader
to get around a rejection. Research has no authority to disable the scan.

The broker forwards only the parent run's existing grants, passes the remaining
deadline and resource ceilings, and charges each child fetch to the parent's
budget. A child cannot mint a fresh unlimited budget. Add this composition when
research is implemented; do not implement research alongside webfetch now.
Research's eventual own result can reuse the envelope with a research-specific
payload schema and preserved source provenance. Its input admission (including
search snippets) and derived-output policy must be designed then; a source's
`no_match` verdict does not certify a newly generated summary.

## Fetch restrictions

- HTTP(S) only, initially ports 80/443, no URL userinfo or authentication.
- Require nonempty, valid deployment destination ranges before readiness or run
  admission; missing/empty/malformed policy fails closed. Validate updates atomically.
- Resolve and validate every destination and redirect. Deny private, loopback,
  link-local, metadata, reserved, and configured cluster/service/node ranges.
  Cover IPv6, IPv4-mapped IPv6, and mixed public/private DNS answers.
- Connect to the validated address while retaining hostname/SNI and certificate
  verification. Do not validate one DNS answer and let the client resolve again.
- Independently enforce egress so a compromised fetcher cannot reach the LAN,
  metadata endpoints, or cluster APIs.
- Disable ambient proxies, cookie persistence, credential forwarding, and
  automatic fetching of linked resources.
- Limit redirects, headers, wire bytes, decompressed bytes, decoding expansion,
  read time and total run time. Enforce limits during receipt and decoding.
- No JavaScript, browser engine, images, PDFs, archives, OCR, or other binary
  processing in v1. Support a documented small set of text media types and charsets.

Initial proposed ceilings: three redirects, 32 KiB total headers, 1 MiB wire body,
1 MiB decoded body, 4 MiB total scan inputs, 15 seconds network time and 5 seconds
inspection. Measure stage startup and set a whole-run deadline within the existing
tool integration timeout before release. These are starting limits, not benchmarks.

## Response inspection

Inspect the full response before any of it reaches the caller:

1. Scan bounded raw headers and entity bytes.
2. Decode supported content encoding and charset without silently accepting
   malformed or ambiguous data.
3. Scan the complete decoded body, including HTML comments, scripts, and hidden
   markup. Nothing is removed before this check.
4. Also scan bounded inspection-only forms that expose HTML entities and Unicode
   obfuscation. These forms help detectors; they do not replace or rewrite the
   returned body. Arbitrary recursive decoding is outside v1.
5. Convert HTML using the local conversion chain below. Inspect every candidate
   output before accepting it or considering another converter.
   Markdown/text conversion removes script/style content; HTML mode returns source.
   Non-HTML text passes through without AI rewriting. This is format conversion,
   not an injection detector. Bound conversion output and resource use.
6. Scan the final returned text fields, including converted content and the final URL,
   and their combined representation. Release only the exact output tied to those
   completed checks. A raw-source match rejects even if conversion would remove it.

All required checks must finish without a match. Detection does not repair the
response: no deleting a suspicious paragraph and returning the rest.
The model cannot request a weaker rule set or ask for the rejected bytes.
Do not automatically retry a rule match.

## Selected conversion approach

Adopt oh-my-pi's native-first HTML cleanup and local extraction fallback. Keep all
conversion inside the offline inspection pod, working on the same downloaded
response. This is a conversion strategy, not a copy of its entire URL reader.

| Order | Converter | Behavior |
|---|---|---|
| 1 | Native HTML-to-Markdown | Use the Rust `html-to-markdown` engine used by oh-my-pi, directly as a Rust crate. Enable content cleanup to remove navigation, forms, headers/footers and script/style boilerplate. Preserve headings, lists, code blocks, tables and useful links. |
| 2 | Local main-content extraction | If native conversion cannot produce usable content, run a fixture-qualified Rust extractor on the already-downloaded HTML. Select the crate during the conversion spike; no claim of Trafilatura-equivalent extraction quality is assumed. |
| 3 | Basic local conversion | If main-content extraction is unsuitable for the page, convert the body without aggressive boilerplate removal. Preserve short pages and structured reference material that an article extractor could discard. |

Trafilatura was named in the earlier draft because oh-my-pi supports it, but it
is not a required dependency. Preserve the native-first/local-fallback behavior
without requiring a Python runtime. Qualify a Rust extractor on the actual
conversion corpus; if none improves results, explicitly record the evidence and
use native cleanup followed by basic conversion rather than shipping an unproven
middle stage.

Pin and fixture-test the selected libraries during implementation. The fallback
order is operator-owned and fixed for the profile; the model chooses only the
output format. Each converter returns content and a fixed method identifier,
never a suggested URL, tool call, or instruction to the caller.

Use deterministic quality checks to choose between converters: empty output,
obvious navigation-only output, and lost document structure should trigger the
next local attempt. Do not reject legitimate short pages merely because they
are below an arbitrary character threshold. Test documentation and tables as
well as articles so cleanup does not silently erase the useful part of a page.

Fallback is allowed for ordinary conversion failure or poor extraction quality.
It is **never** allowed after a detection hit, inspection failure, timeout, crash,
or resource-limit failure. Scan every produced candidate before evaluating its
quality; a match rejects the response even if another converter would omit it.
All attempts share the same total deadline and resource budgets.

For `text`, use local plain-text output with the same cleanup/fallback policy.
For `html`, return the decoded source as inert text after inspection; this format
skips conversion, never scanning. Non-HTML text passes through without rewriting.
No mode executes scripts, loads images, or follows links found in the page.

oh-my-pi also has site-specific handlers, alternate-page requests, remote readers
and artifact-based truncation. Those remain outside this initial conversion
implementation. In particular, no Jina/Firecrawl/Parallel request or URL-fetching
subprocess may escape the offline boundary. Additional formats or handlers need
their own bounded design; they are not implicit conversion fallbacks.

## YARA support

**YARA is a useful additional detector for recognizable text/byte patterns.**
Propose YARA-X behind a small detector interface with three results:
`NO_MATCH`, `MATCH`, and `ERROR`. Any MATCH rejects the complete response;
any ERROR withholds it. There is no voting or model override.

Use a pinned engine and a small reviewed rule bundle, with optional
operator-maintained bundles. Support a tested subset of YARA syntax; do not
promise all existing YARA rules work unchanged. The official
[YARA-X Rust API](https://virustotal.github.io/yara-x/docs/api/rust/)
supports direct integration through the `yara-x` crate. Pin and test the compiler,
scanner and resource-control APIs used by the implementation.

- Compile and validate rule bundles before deployment.
- Disable includes and modules in the initial supported subset.
- Mount immutable bundles read-only; record engine and bundle versions per run.
- Cap rule size/count, compilation resources, scan work, and match counts.
- Use a supervising deadline that can terminate a hung scanner.
- Do not load rules or compiled artifacts from a fetched response.
- Promote rule changes only after offline corpus checks. Failed updates leave the
  previously active valid bundle in place and report an operator error. With no
  valid bundle, the tool is unavailable.
- Every enabled rule is enforced. No per-request bypass or warn-and-return mode.

YARA matches configured patterns, not intent. Some instructions may not match,
and legitimate documentation may match. Treat it as an extra check and tune the
bundle for normal use; a non-match leaves the content labeled as external data.

The existing HearthAI rule that external data does not grant authority still
applies. This fetch implementation adds no planner, summarizer, research loop,
new action-approval mechanism, or session-taint framework.

## Delivery, storage, and packaging

Deliver the validated envelope through the tool adapter; admitted content enters
the LLM only as external tool data. Human-facing rendering is a separate concern:
the UI may format admitted Markdown using ordinary browser safety controls, but
must not automatically load response-specified resources or re-fetch the URL
through a native loader. Rendering is not a substitute for the LLM ingestion gate.

A rejection withholds bytes from model context, including model-visible errors,
previews and citations. It is not a content-rating or human-safety verdict. A human
may independently inspect the page outside model context; no human-viewing tool
or raw quarantine UI is added here. Human inspection or approval of the URL does
not turn a rejected response into admitted model input. Any future human-only
diagnostic path must remain separate from model-accessible tools and context.

Record run ID, outcome, bounded rule IDs, policy versions, byte counts and timings.
Keep bodies, full URLs, headers, matched strings, and worker exception text out of
logs, metrics and durable audit records. No raw-content cache or quarantine viewer
in v1. The caller may retain successfully returned content in its normal
conversation history; that is distinct from temporary fetch storage.

HearthAI owns the fixed worker images, rule bundle, profile, result handling,
application policies and tool registration in its chart. home-ops supplies the
cluster/network inputs. This proposal requires the relevant `ai-jobs` runtime
pieces to exist; it does not treat scaffolding as a deployed executor.

## Acceptance

A permitted URL returns its content in the requested format after all checks pass.
A matching rule, incomplete scan, forbidden destination, or exceeded limit
returns a fixed error with none of the response content. Tests check the isolation and return/rejection behavior using representative fixtures.

## Existing harness behavior informing the proposal

Source review on 2026-09-12; these are observations of the named fetch paths,
not a security audit of either complete application.

| Harness | Observed fetch behavior | Implication for HearthAI |
|---|---|---|
| OpenCode | Defaults to Markdown; supports text/HTML. Uses Turndown for HTML-to-Markdown and a parser for text extraction. Checks a 5 MiB response limit and exposes a timeout capped at 120 seconds. | Deterministic conversion is ordinary fetch behavior; adopt a similarly small format contract. |
| oh-my-pi | URL reads try site-specific handlers, alternate Markdown/feed resources and reader backends. Its default reader order starts with local native HTML-to-Markdown, then other local/remote fallbacks. Supports raw mode; large returned results are truncated with an artifact reference. | Selected reference for native-first cleanup and local extraction fallback. Automatic alternate requests, remote readers and artifact workflows are outside this first fetch tool. |

Neither inspected fetch path includes a YARA/prompt-injection match gate that
rejects an entire response before returning it. Removing scripts or navigation
is content cleanup, not detection of instructions directed at an LLM.
HearthAI's proposed addition is isolated processing plus strict rejection.

OpenCode source: [webfetch.ts](https://github.com/anomalyco/opencode/blob/95daf90670b7c039c436c85537da5fbfe2205b41/packages/opencode/src/tool/webfetch.ts).
oh-my-pi sources: [fetch.ts](https://github.com/can1357/oh-my-pi/blob/540a7292d903723558a807fcb95c687f72d015f3/packages/coding-agent/src/tools/fetch.ts),
[native HTML conversion](https://github.com/can1357/oh-my-pi/blob/540a7292d903723558a807fcb95c687f72d015f3/crates/pi-natives/src/html.rs).

Josh selected the oh-my-pi conversion approach after reviewing this comparison.
The implementation steps are recorded in the linked plan; this document does not
claim the runtime has been implemented.
