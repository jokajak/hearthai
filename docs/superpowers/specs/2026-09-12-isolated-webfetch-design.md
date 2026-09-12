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
follow-up requests. Web research and new conversation-wide authorization systems
are outside this change. The existing research plan is unchanged.

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

## Threat model

The protected asset is the model's context and any action the model takes as a
result of reading it, together with the operator's network and cluster. The human
operator is not the protected party: they choose the URL and read the answer, but
the model ingests the page.

**A human in the loop does not remove the need for inspection.** A language model
has no enforced boundary between content supplied as tool output and direction
supplied by the operator. Retrieved text enters the same context as the request
and is available to influence the next action. Operator URL selection limits an
attacker's ability to choose their victim; it does nothing once a page the
operator legitimately wanted carries hostile text. This tool exists to put a gate
in front of that ingestion, and to be the single fetch path that later
capabilities — web research among them — are built on rather than bypass.

### Attackers, and what each control actually stops

| Attacker | Goal | Control | Not stopped by it |
|---|---|---|---|
| Hostile response aimed at the parsers | Code execution in the worker | Offline inspection pod with no Internet access; non-root, dropped capabilities, read-only root, bounded CPU/memory/time | A compromised scanner can still lie about its own verdict; the result gate trusts a verdict it cannot independently verify |
| Hostile page or redirect chain | Reach the LAN, metadata endpoints, or cluster APIs | Destination validation with per-redirect DNS pinning, plus independently enforced egress | Nothing, if the configured cluster ranges are empty or wrong — see *Known fail-open* below |
| Opportunistic injection | Steer any model that reads the page | Rule bundle; whole-response rejection | Anything it has no pattern for |
| Targeted injection | Steer this model, knowing this gate exists | — | Paraphrase, translation, novel encoding, and instructions carrying no imperative form. Assume this attacker succeeds |
| Over-broad or compromised rule bundle | Withhold content the operator needs | Reviewed bundles, offline corpus checks before promotion, recorded engine and bundle versions per run | An operator-authored rule that is merely too broad. This is an availability risk the operator owns, not an attack the tool detects |

### Opportunistic and targeted injection are different problems

The detection value of this design rests entirely on the first. Stating the split
is what makes the claim testable:

- **Opportunistic injection** is mass-deployed and not aimed at HearthAI: text
  planted in scraped-content farms, SEO pages, comment sections, wiki edits, issue
  threads, and poisoned documentation mirrors. It reuses published phrasings
  because that is cheap, not because its author tested it against a gate. Pattern
  matching works here for the same reason signature antivirus still works on
  commodity malware, and this is the large majority of what a personal deployment
  meets.
- **Targeted injection** is written by someone who knows a rule gate sits in front
  of this tool. Recall against it is approximately zero, and no rule bundle changes
  that. *Response inspection* bounds normalization deliberately, so encodings
  outside those bounded forms pass by construction.

**This tool raises the cost of drive-by injection. It does not defend against a
motivated attacker who is aiming at this system.** Every statement about
inspection elsewhere in this document means that and nothing more.

### What "a fetch result you can trust" means

A successful result is a process guarantee, not a judgement about the content:
every required check completed, and no part of the response reached the caller
unless all of them passed. It is not a statement that the page is safe.

The complementary guarantee matters just as much for a tool on the critical path —
that a legitimate page is not silently withheld — and is bounded by the
false-positive budget below.

### Measured before release, not asserted

The claims above are testable, and this document's acceptance criteria are not
satisfied by narrative:

- **Recall against opportunistic injection.** Measure the active bundle against a
  corpus of published injection strings and their common variants. Record the
  number.
- **Recall against targeted injection.** Measure against paraphrased, translated
  and encoded rewrites of that same corpus. The expected result is near zero.
  Record it, so no later reader mistakes this gate for a defense against it.
- **False-positive rate.** Measure against a corpus drawn from the operator's real
  reading: CVE writeups, agent-security papers, framework documentation containing
  system-prompt examples, issue threads quoting attacks. **A ship/no-ship
  threshold belongs here and this document does not yet carry one; set it before
  implementation begins.** A tool that withholds material the operator needs will
  be worked around, and a bypassed gate is worth less than no gate, because the
  bypass is undocumented.

### Known fail-open

*Fetch restrictions* denies "configured cluster/service/node ranges". An empty or
misconfigured range list therefore fails open, while a missing rule bundle
correctly makes the tool unavailable. Make destination configuration fail closed
the same way: absent or unparseable range configuration makes the tool
unavailable rather than unrestricted.

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
caller while fetching or scanning. A sandbox does not prove a compromised scanner's
verdict is honest; keep dependencies small and maintained.

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

A successful tool result contains:

- `status: "ok"`;
- the validated final URL and HTTP status;
- supported content type and retrieval time;
- `content`: the response in the requested format, without summarization;
- `format` and a fixed `conversion_method` identifying the converter used;
- `trust: "external_untrusted"` and `inspection: "no_match"`.

The trust marker describes fetched data; it grants no permissions. A 4xx/5xx HTTP
response may be returned with its actual status if its text body passes the same
checks. Transport failure and inspection failure are separate tool errors.

Every rejection/error has a fixed code and message, with no remote body, title,
headers, URL, matched text, or worker exception embedded in the error. For example:

```json
{"status":"rejected","code":"content_rejected","message":"Response content was withheld."}
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

## Fetch restrictions

- HTTP(S) only, initially ports 80/443, no URL userinfo or authentication.
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

YARA cannot reliably identify arbitrary semantic instructions. Paraphrases,
multilingual text, and novel encodings can evade patterns. Conversely, legitimate
security documentation can match an active rule and be rejected. Track those
tradeoffs in the rule tests; do not call an unmatched page “safe.”

The existing HearthAI rule that external data does not grant authority still
applies. This fetch implementation adds no planner, summarizer, research loop,
new action-approval mechanism, or session-taint framework.

## Delivery, storage, and packaging

Return content as an escaped/inert tool string. Do not render HTML or automatically
load images, links, or previews from the response. Verify the tool adapter preserves
that behavior; it must not re-fetch the URL through a native loader.

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
returns a fixed error with none of the response content. Tests demonstrate both
the isolation boundary and the known limits of pattern detection.

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
