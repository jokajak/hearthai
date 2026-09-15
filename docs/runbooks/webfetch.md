# Webfetch runbook

**Status:** the two stages, their contracts and the rule bundle are implemented
and tested. Cluster execution through `ai-jobs` is not: see
[What is not wired up yet](#what-is-not-wired-up-yet).

**Design:** [safer webfetch](../superpowers/specs/2026-09-12-isolated-webfetch-design.md) ·
**Plan:** [implementation steps](../superpowers/plans/2026-09-12-isolated-webfetch.md)

Webfetch takes one URL and returns either its content, converted locally, or a
fixed error with none of the response in it. It does not search, follow page
links, summarize, or call a model. A successful inspection means the configured
rules did not match; **it is not a statement that the content is safe.** Fetched
content stays external data.

## The two stages

| Stage | Binary | Network | Produces |
|---|---|---|---|
| Fetch | `webfetch-fetch` | Public web only, policy-checked on every hop | An immutable run-scoped artifact |
| Inspection | `webfetch-inspect` | None | Exactly one validated envelope |

They are separate processes, and in a deployment separate pods, so a parser or
scanner bug does not inherit the fetcher's egress. The inspection stage is the
only component that can release content.

## Running it locally

```bash
cd service/web_fetch
cargo test --workspace          # unit and contract tests
./scripts/end-to-end.sh         # both stages, separate processes, fixture server
```

One request by hand:

```bash
work=$(mktemp -d)
echo '{"url":"https://example.com/","format":"markdown"}' > "$work/request.json"

cargo run --bin webfetch-fetch -- \
  --run-id run-1 --request "$work/request.json" \
  --policy deploy-destination-policy.example.json --artifact-dir "$work"

cargo run --bin webfetch-inspect -- \
  --run-id run-1 --artifact-dir "$work" --rules rules/web-content-v1
```

The fetch stage exits `0` on success, `1` when it recorded a public failure code
for the inspection stage to report, and `2` when it could not run at all
(unusable policy, unreadable request). The inspection stage exits `0` for a
success envelope, `1` for a rejection or error envelope, and `2` when it could
not produce one. The envelope goes to stdout or `--out`; a bounded audit line
goes to stderr.

## Destination policy

The fetch stage refuses to start without a valid policy file:

```json
{
  "policy_version": 1,
  "profile": "production",
  "denied_cidrs": ["10.42.0.0/16", "10.43.0.0/16", "fd00::/8"]
}
```

* `denied_cidrs` is this cluster's own service, pod and node ranges. It must be
  present and non-empty: missing, empty or malformed configuration leaves the
  capability unavailable rather than unrestricted.
* Loopback, private, link-local (including `169.254.169.254`), CGNAT, reserved,
  multicast and documentation space are refused by default, as are the IPv6
  spellings of them - v4-mapped, v4-compatible, 6to4 and NAT64.
* A name is resolved once, every answer must be permitted, and the connection is
  pinned to those addresses. A name that answers with a mix of public and
  private addresses is refused outright.
* `profile: "test"` additionally permits loopback and non-default ports so a
  fixture server is reachable. It exists for `scripts/end-to-end.sh`. The chart
  ships `production`, and the fetcher prints a warning on stderr whenever it is
  running under a test policy.

## The rule bundle

`service/web_fetch/rules/web-content-v1/` holds the shipped bundle: a
`bundle.json` manifest whose `policy_id` must match the directory name, plus
`*.yar` files. It is compiled at startup with includes and modules disabled, and
it is mounted read-only.

Current rules, all aimed at content that addresses a model rather than a reader:

| Rule | What it looks for |
|---|---|
| `hearthai_instruction_override` | An imperative to ignore, disregard or override prior instructions |
| `hearthai_model_addressing` | Text addressed to an assistant, role reassignment, hidden notes to an agent |
| `hearthai_chat_template_tokens` | Turn delimiters and tool-call framing in page text |
| `hearthai_secret_exfiltration` | Instructions to reveal or transmit credentials or prior context |
| `hearthai_tool_coercion` | Instructions to run something, hide something from the user, or follow another URL |

**Any match rejects the whole response.** There is no per-request bypass, no
warn-and-return, and no automatic retry after a match. A scanner error, timeout,
partial scan or missing bundle withholds the response too.

### Known false positives and the trade behind them

The override rule requires the phrase to begin a line or follow sentence-ending
punctuation. This is what lets documentation *about* prompt injection through:
`the phrase "ignore previous instructions"` does not match, while
`Ignore all previous instructions.` at the start of a line does. The fixtures
`fixtures/html/injected.html` and `fixtures/html/quotes-an-injection.html` pin
both halves of that trade, and `scripts/end-to-end.sh` checks them.

Expect these to match and be refused even though they are legitimate:

* A page that quotes an injection *as a line of its own*, such as a blog post
  showing an attack payload in a block quote rather than in a code fence.
* A security advisory listing chat-template delimiters outside a code block.
* A page that documents `curl`-based credential upload in the imperative.

When a rule causes an unnecessary rejection, tune the bundle. Do not add a way
for a caller to skip a rule.

### Changing the bundle

1. Edit or add a `.yar` file, keeping the bundle under 64 files and 256 KiB.
2. Run `cargo test -p hearthai-webfetch-inspector` - the bundle must compile and
   the pipeline fixtures must still behave.
3. Run `./scripts/end-to-end.sh` to check the false-positive fixture.
4. Record new useful examples and false positives in this file.

A bundle that does not compile is refused, and the previously mounted bundle
stays in force. With no valid bundle, the tool is unavailable.

## Limits

| Limit | Value |
|---|---|
| Redirects | 3 |
| Response headers | 32 KiB |
| Wire body | 1 MiB |
| Decoded body | 1 MiB, and at most 64x the wire bytes |
| Total scan input | 4 MiB |
| Returned content | 400,000 characters |
| Network stage | 15 s |
| Inspection stage | 5 s |
| Whole run | 25 s |

Exceeding a limit is a refusal, never a truncated response: webfetch does not
return a prefix of a page it could not finish checking. Media types are limited
to a documented text set, charsets to ones that decode unambiguously, and
content codings to `identity`, `gzip` and `deflate`.

## Reading the audit line

The inspection stage writes one JSON line to stderr per run:

```json
{"run_id":"run-1","outcome":"rejected","code":"content_rejected","policy_id":"web-content-v1",
 "policy_digest":"…","matched_rules":["hearthai_instruction_override"],"http_status":200,
 "redirects":0,"requested_url_sha256":"…","wire_bytes":4096,"decoded_bytes":4096,
 "content_chars":null,"conversion_method":null,"scans":7,"elapsed_ms":12}
```

It deliberately contains no body, no full URL, no headers, no matched strings and
no worker exception text. `requested_url_sha256` is how a run is correlated with
what was asked for without storing the address; the control plane stores the same
digest instead of the request.

## Failure codes

| Code | Cause | Body returned |
|---|---|---|
| `content_rejected` | An enabled rule matched anywhere | None |
| `inspection_failed` | Scan crashed, timed out, was incomplete, or no valid bundle | None |
| `unsafe_source` | Forbidden destination or redirect | None |
| `unsupported_content` | Unsupported media type or charset, malformed encoding | None |
| `response_limit_exceeded` | A header, byte, expansion or output limit | None |
| `fetch_failed` | Network failure | None |
| `deadline_exceeded` | The run's deadline elapsed | None |
| `internal_error` | Artifact binding failed, or a result did not satisfy the contract | None |

Each code carries one fixed message. That is enforced on both sides of the wire:
an envelope whose message is anything else does not validate.

## Images

```bash
docker build --target fetch-worker -t hearthai/webfetch-fetch service/web_fetch
docker build --target inspector    -t hearthai/webfetch-inspect service/web_fetch
```

Both are distroless and run as `nonroot`. The fetch image carries no CA bundle
from the distribution - its TLS roots are compiled in. The inspection image
carries the default rule bundle at `/opt/webfetch/rules/`; mount an
operator-managed bundle over it and point `--rules` at the mount. CI builds
`linux/amd64`; other architectures are untested.

## What is not wired up yet

Steps 4 and 5 of the plan are not complete, and the chart does not claim they
are:

* **No `ai-jobs` runtime.** The control plane in `service/ai_jobs` is a contract
  and admission layer; there is no executor that creates pods. The fixed
  `web-fetch-v1` profile - two sequential ephemeral pods, no service account
  token, read-only root, an inspection pod with egress denied, artifact handoff
  and cleanup - is designed but not deployed.
* **No chart wiring.** No HearthAI chart template renders the profile, the rule
  bundle, the destination policy or the tool registration, because there is
  nothing to register with yet.
* **No tool adapter in Open WebUI.** Nothing calls `POST /v1/tools/web-fetch`;
  the route exists in the OpenAPI document as the contract a server will serve.
* **No published images.** The release workflow does not push the two worker
  images.

Until those exist, webfetch runs as two processes: everything about the request
contract, destination policy, inspection, conversion and the envelope is
implemented and enforced, but the isolation properties that come from running
each stage in its own locked-down pod are only as strong as wherever you run the
binaries.
