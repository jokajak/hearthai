# webfetch runbook

**Tool:** `web_fetch:v1` · **Endpoint:** `POST /v1/tools/web-fetch` ·
**Policy:** `web-content-v1`

Design: [safer webfetch](../superpowers/specs/2026-09-12-isolated-webfetch-design.md).
Plan: [implementation steps](../superpowers/plans/2026-09-12-isolated-webfetch.md).

This runbook covers the parts an operator owns: the two configuration inputs
that have no safe default, the limits, what each failure means, and how to
change the rule bundle without losing detection.

## What the tool does, and what it does not claim

One URL in; either its content in the requested format, or a fixed error with
none of the content. It does not search, choose sources, follow links, retry a
rejection, summarise, or call a model. HTTP redirects are the only automatic
follow-up requests.

The inspection gate protects what enters the model's context. **A non-match is
not a safety verdict.** It means the configured rules did not fire. Admitted
content is still external data and still labelled `external_untrusted`; the
existing rule that external data grants no authority is unchanged by this tool.

A rejection is also not a content rating. It withholds bytes from model
context. A person may open the page themselves; that does not turn a rejected
response into admitted model input, and there is no quarantine viewer or raw
retrieval path in v1.

## Configuration that fails closed

Two inputs have no default. If either is missing, empty or malformed, the
capability is unavailable and the service should not report ready.

### Destination policy

The deployment's own cluster, service and node ranges, which only home-ops
knows. Everything else non-public - loopback, private, link-local (including
the metadata endpoint at `169.254.169.254`), and the IPv6, IPv4-mapped and
6to4 spellings of all of them - is denied by classification and needs no
configuration.

```json
{"cluster_denied_cidrs": ["10.42.0.0/16", "fd12:3456::/32"]}
```

An update is applied atomically: a document with one bad entry changes nothing
and leaves the previous policy enforcing.

### Rule bundle

A read-only mount containing `bundle.json` and the `.yar` files it names:

```json
{"policy_id": "web-content-v1", "bundle_version": 1, "rules": ["prompt-injection.yar"]}
```

With no valid bundle, the tool is unavailable. There is no warn-and-return mode
and no per-request bypass.

## Limits

| Limit | Value | Stage |
|---|---|---|
| Redirects | 3 | fetch |
| Total headers | 32 KiB | fetch |
| Wire body | 1 MiB | fetch |
| Network time | 15 s | fetch |
| Decoded body | 1 MiB | inspection |
| Total scan input across every scan and candidate | 4 MiB | inspection |
| Inspection time | 5 s | inspection |
| Returned content | 1 MiB | result gate |

These are starting ceilings, not benchmarks. Measure pod startup plus fetch and
inspection latency against the tool integration timeout before release; the
tool's own budget is 45 s of wall clock.

Supported media types are text only: `text/html`, `text/plain`,
`text/markdown`, `text/x-markdown`, `text/csv`, `text/xml`,
`application/xhtml+xml`, `application/xml`, `application/json`. Supported
charsets are UTF-8, US-ASCII, ISO-8859-1/windows-1252 and UTF-16. No
JavaScript, browser engine, images, PDFs, archives or OCR.

## Failures

Every failure is a fixed code with a fixed message. The message is derived from
the code and never composed from a body, title, header, URL, matched string or
worker exception.

| Code | Meaning | Operator action |
|---|---|---|
| `content_rejected` | An enabled rule matched somewhere in the response | Check whether the page is a legitimate false positive; tune the bundle, never the request |
| `inspection_failed` | A scan crashed, timed out, was incomplete, or the bundle is missing | Check the bundle mounted and compiles; check the inspection budget |
| `unsafe_source` | The destination or a redirect target is not an allowed public address, or no destination policy is configured | Check the destination policy is mounted and non-empty |
| `unsupported_content` | Media type, charset or content-encoding outside the supported set, or a body that does not decode cleanly | Usually the page; no action |
| `response_limit_exceeded` | A header, redirect, body or decoding ceiling was reached | Expected for large pages and compressed bombs |
| `fetch_failed` | Transport failure, or an artifact that failed its own binding checks | Check egress; a repeated artifact failure is a substrate problem |
| `deadline_exceeded` | The run's budget ran out | Check stage startup latency against the budget |

`content_rejected` and `inspection_failed` are deliberately distinct
internally - one is the gate working, the other is the gate not finishing - and
both withhold the entire response.

## Rule maintenance

Change rules in the bundle, not in the request. The model has no way to ask for
a weaker rule set, and there is no reason to add one.

1. Edit or add a `.yar` file and list it in `bundle.json`.
2. Run `cargo test -p hearthai-web-fetch-inspector --test rules` from
   `service/web_fetch`. That test is the offline corpus check: it asserts the
   bundle compiles under the supported subset, that each pattern the bundle
   claims to cover fires, and that the ordinary-page corpus stays clean.
3. Add the page that motivated the change to whichever list it belongs in.
4. Promote. A bundle that fails to compile leaves the previous valid bundle in
   place and reports an operator error.

The supported subset is plain strings and regular expressions. Includes and
modules are disabled; a bundle that needs one does not compile.

### Recorded false positives

These match and are withheld. They are recorded rather than fixed, because no
pattern distinguishes a page explaining an injection from a page performing one:

- Security documentation quoting `Ignore all previous instructions`.
- Prompt-engineering write-ups containing chat control tokens such as
  `<|im_start|>`.

If one of these blocks work that matters, the fix is an operator decision about
the bundle - narrow the rule, or accept that the page is not fetchable by the
model - and never a bypass on the request.

## Residual risks

Stated plainly, because the tool's value depends on not overclaiming:

- **YARA matches patterns, not intent.** An instruction phrased outside the
  bundle passes. There is no detection-rate target and none is implied.
- **Conversion is not detection.** Removing `<script>` and navigation is
  cleanup. The scan over the raw source is what catches an instruction that
  cleanup would have removed.
- **Normalization is bounded.** Entities are undone once and invisible
  characters removed once. Arbitrary recursive decoding is outside v1, so
  layered obfuscation can still get through.
- **The envelope is an integration contract, not a prompt boundary.** It
  labels provenance so a consumer cannot flatten a response into unlabeled
  trusted instructions. A model can still be influenced by content that was
  admitted.
- **Human rendering is a separate concern.** The UI may format admitted
  Markdown with ordinary browser safety controls, but must not automatically
  load response-specified resources or re-fetch the URL through a native
  loader. Rendering is not a substitute for the ingestion gate.

## What is not implemented yet

The two worker stages and both wire contracts exist and are tested. What the
plan still calls for, and this repository does not yet have, is the substrate
that runs them: the fixed two-pod profile, the artifact transfer between pods,
the network policies that keep the inspector offline and the fetcher out of the
cluster, image builds, chart packaging and tool registration in the deployment.
Until those land, `web_fetch:v1` is a validated contract and a pair of tested
libraries, not a deployed capability.
