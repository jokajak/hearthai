# Safer webfetch

**Status:** proposed design; no runtime implementation.
**Date:** 2026-09-12.
**Plan:** [implementation steps](../plans/2026-09-12-isolated-webfetch.md).

## Scope

Implement one tool: accept a URL, fetch its response in an isolated environment,
check it, and either return the fetched content or reject the entire response.

This is a fetch tool. It does not search, select sources, follow page links,
summarize, extract answers, or call an LLM. HTTP redirects are the only automatic
follow-up requests. Web research and new conversation-wide authorization systems
are outside this change. The existing research plan is unchanged.

The returned content is the decoded response body. HTML is returned as HTML source
in a text field, not rendered or executed. No “projected content,” selected excerpts,
or AI rewriting. If the complete body exceeds the tool's limit, reject it; do not
return a scanned prefix.

**Any enabled detection rule that matches causes rejection of the whole response.**
A failed or incomplete inspection also returns no content. A successful inspection
means only that the configured rules did not match; the response remains untrusted.

## Execution

Use the existing `ai-jobs` admission and fixed-profile execution boundaries.
The model supplies the URL, never images, commands, rules, credentials, networking,
or pod configuration.

The fixed webfetch profile has two sequential stages:

1. **Fetch pod:** downloads one bounded HTTP(S) response into temporary storage.
   It has constrained public-web egress and no model, memory, provider credentials,
   host mounts, or Kubernetes API access.
2. **Inspection pod:** reads the completed immutable response, decodes and scans it.
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
{"url":"https://example.com/page"}
```

Reject unknown request fields. V1 uses GET with fixed headers and supports public
HTTP(S) text responses. No caller-supplied headers, cookies, credentials,
skip-scan option, or rules.

A successful tool result contains:

- `status: "ok"`;
- the validated final URL and HTTP status;
- supported content type and retrieval time;
- `content`: the complete decoded response body;
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
| All required scans complete without matches | Successful result | Complete decoded body |

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
5. Scan the final returned text fields, including the final URL, and their combined
   representation. Release only the exact body tied to those completed checks.

All required checks must finish without a match. Detection does not repair the
response: no deleting a suspicious paragraph and returning the rest.
The model cannot request a weaker rule set or ask for the rejected bytes.
Do not automatically retry a rule match.

## YARA support

**YARA is a useful additional detector for recognizable text/byte patterns.**
Propose YARA-X behind a small detector interface with three results:
`NO_MATCH`, `MATCH`, and `ERROR`. Any MATCH rejects the complete response;
any ERROR withholds it. There is no voting or model override.

Use a pinned engine and a small reviewed rule bundle, with optional
operator-maintained bundles. Support a tested subset of YARA syntax; do not
promise all existing YARA rules work unchanged. The official
[YARA-X Python API](https://virustotal.github.io/yara-x/docs/api/python/)
provides compilation, byte scanning, namespaces, and scan timeouts.

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

A permitted URL returns its complete decoded content after all checks pass.
A matching rule, incomplete scan, forbidden destination, or exceeded limit
returns a fixed error with none of the response content. Tests demonstrate both
the isolation boundary and the known limits of pattern detection.
