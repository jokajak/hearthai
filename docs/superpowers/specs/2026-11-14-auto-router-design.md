# The `auto` router

**Status:** proposed, for owner approval (gate G5). Nothing here is implemented.
**Date:** 2026-11-14.
**Plan items:** I5 (router hook in shadow mode) and I6 (enable the cloud branch), Milestone 3 of
[`2026-11-13-local-first-increments.md`](../plans/2026-11-13-local-first-increments.md). Package S1
of [the orchestration brief](../plans/2026-11-13-local-first-orchestration.md).

The plan's Milestone 3 is the brief and is not repeated: `auto` is a gateway model name; a pre-call
hook inspects the whole message list on every request; rules, then a local classifier, then
capability; the fail-closed table is the contract. This spec settles what the brief leaves open:
the rule set and where the term list comes from, the classifier prompt and its exact accepted
answer, how the hook reaches the local model without going back through itself, what is logged,
and how shadow mode is switched. Facts about LiteLLM are from v1.106.6 source, the version
home-ops runs.

## Decisions to approve

| # | Decision | Alternative not taken, and why |
|---|---|---|
| R1 | **`auto` is its own catalogue entry with the same backend as `local-qwen`** (`ollama_chat/<model>` through the gate). The hook never sends `auto` anywhere except by rewriting the request's `model` to the configured cloud default, on one code path, in enforce mode. Everything else leaves `auto` on its own local deployment, or raises. So if the hook is missing, not loaded, or disabled, `auto` is simply a second name for the laptop. | An `auto` entry pointing at nothing (fails if the hook is absent): it would also refuse every request in shadow mode, which then logs nothing useful. Rewriting `auto` → `local-qwen`: two names for the same choice, and the router would need access to `local-qwen` on the caller's key. |
| R2 | **The hook is `async_pre_call_hook` on a `CustomLogger`** loaded from `litellm_settings.callbacks: ["hearth_router.proxy_handler"]`, a module mounted next to `config.yaml`. It runs after key authentication (which checks the requested name, `auto`) and before routing, on Chat Completions, Responses and Anthropic Messages, and returns the possibly modified `data` or raises. It acts **only** when `data["model"] == "auto"`; for every other name it returns `data` unchanged, without reading the messages. | `async_pre_routing_hook` / `async_filter_deployments`: run inside the router, after the name is fixed; they can narrow candidates, not choose between `auto`'s local deployment and a cloud name. LiteLLM's semantic `auto_router/`: needs an embedding call and routes by similarity, not fail-closed (the plan already rejects it). |
| R3 | **The classifier talks to Ollama directly through the gate** (`POST http://<fullname>-local:11434/api/chat`, stdlib HTTP, short timeouts), not through LiteLLM. It cannot recurse into the hook, cannot be rerouted, and the gate's fixed 503 tells the hook the laptop is down. | `litellm.acompletion("local-qwen", …)` from inside the hook: re-enters the proxy's router and callbacks, so a future hook change could loop or reroute the classifier itself. |
| R4 | **The classifier answer is constrained by Ollama structured output.** `format` is the JSON schema `{"type":"object","properties":{"verdict":{"enum":["PUBLIC","PRIVATE"]}},"required":["verdict"]}`, with `think: false`, `temperature: 0` and `num_predict: 16`. Cloud is permitted only if the response parses as exactly `{"verdict": "PUBLIC"}`. Any other byte, an extra key, a thinking block, a timeout or an HTTP error means local. | Free-text `PUBLIC`/`PRIVATE` with string matching: works, but the schema makes malformed output rarer and the acceptance test exact. Free text stays the fallback if the pinned Ollama rejects `format` with a schema, which the package checks. |
| R5 | **The term list is a Secret**, one term per line (`#` comments, blank lines ignored), mounted read-only into the LiteLLM pod. Matching is case-insensitive on word boundaries, after Unicode NFKC normalisation. Terms are never logged; a match logs only `rule:term`. If the file is missing, unreadable or empty, the hook **refuses** every `auto` request ("term list missing"), per the table. | Terms in a ConfigMap or in Git: the list is itself sensitive (names, places, employers). |
| R6 | **Shadow mode is a chart value**, `router.mode: shadow` (default) or `enforce`, rendered into the hook's config file. In shadow mode the hook computes the full decision and logs it, but never rewrites the model: `auto` stays local or refuses. Refusals apply in both modes. | An environment variable: it changes nothing the chart test can see; a file in the same ConfigMap as the module is one diff to review. |
| R7 | **Capability is size only in v1.** Public content whose token estimate (LiteLLM's local `token_counter`, the same one `/utils/token_counter` uses) exceeds 90% of the advertised local context goes to the cloud default; anything smaller stays local. No other heuristics until the shadow week shows a need. | Keyword heuristics ("summarise this long…", code detection): guesses that make the decision harder to explain and test. |
| R8 | **The cloud default is one configured catalogue name**, `router.cloudDefault`, validated at render time against the catalogue. Recommended: `claude-sonnet-5`. | Several cloud targets: a second decision nobody needs yet. |

## Rules (step 1, deterministic)

Applied to the text of **every** message in the request, every role (system prompts from personas
included), and to every content part. Any match: local.

| Code | Matches |
|---|---|
| `rule:marker` | `#private` anywhere, case-insensitive |
| `rule:attachment` | any non-text content part: image, file, document, audio, `input_image`, `image_url`, Anthropic `image`/`document` blocks |
| `rule:credential` | private-key blocks (`-----BEGIN … PRIVATE KEY-----`); AWS access keys (`AKIA`/`ASIA` + 16); GitHub tokens (`gh[pousr]_…`, `github_pat_…`); OpenAI/Anthropic-style keys (`sk-…` ≥ 20, `sk-ant-…`); Slack tokens (`xox[abposr]-…`); JWTs (three base64url segments, first decoding to a JSON header); `password`, `passwd`, `secret`, `api_key`, `token` followed by `:` or `=` and a non-space value |
| `rule:identity` | US SSN (`\d{3}-\d{2}-\d{4}`, excluding 000/666/9xx areas); payment card numbers (13–19 digits, spaces or dashes, Luhn-valid); IBAN (country code + check digits + valid mod-97); email addresses; phone numbers in E.164 or NANP form |
| `rule:term` | any line of the term list (R5) |

The rule table is a literal in the module with one test per row, positive and negative. A rule that
is too eager costs a local answer, never a leak.

## Classifier (step 2)

**What it sees.** Each `user`-role message's text, separately, newest first; system and assistant
messages are covered by the rules only. Verdicts are cached in process memory by
`sha256(text)`, so a conversation's earlier turns are classified once. A message over 4,000
characters is not classified: it is `classifier:too_long`, which means local.

**Prompt** (system, fixed in the module, versioned with it):

> You are a privacy filter for a household AI system. Decide whether the user text below contains
> any personal, household, health, financial, legal, employment, location or credential information
> about real people, or anything a family would reasonably want to keep at home. If you are unsure,
> answer PRIVATE. Reply only with the JSON object required by the schema.

User message: the text, unchanged. Accepted answer: R4.

**Timeouts.** Connect 2 s (the gate fails faster when the laptop is down), total 20 s per message.
A gate `503` with `local_unavailable` means **laptop offline**, not "classifier error": the whole
request refuses with the plan's message.

**Cost, stated.** The laptop serves one request at a time with one cache slot, so a classifier call
evicts the chat's cached prompt prefix: the next turn re-evaluates its whole history (about 30 s
for 20k tokens). Short conversations barely notice; long ones will. Shadow mode logs classifier
latency so the week of use measures it. If it is too slow, the options are a second slot
(`OLLAMA_NUM_PARALLEL=2`, which costs memory the laptop does not have spare) or a small separate
classifier model (which `OLLAMA_MAX_LOADED_MODELS=1` would swap in and out). Neither is chosen now.

## Fail-closed table (the contract; each row is a unit test for all three request formats)

| Situation | Shadow mode | Enforce mode |
|---|---|---|
| Any rule matches | local (logs `rule:*`) | local |
| Classifier `PRIVATE`, malformed, error, timeout, too long | local | local |
| Laptop offline (gate 503 `local_unavailable`, or connect failure) | refuse: "Automatic routing needs the laptop. Choose a cloud model yourself if this is not private." | same |
| Term list missing or empty; config file missing or invalid; hook raises | refuse ("Automatic routing is unavailable: <fixed reason>") | same |
| Private (any rule or not `PUBLIC`) and over the local context | refuse: "too large for the local model" | same |
| Rules clean, every user message `PUBLIC`, within local context | local (`public:local`) | local |
| Rules clean, every user message `PUBLIC`, over 90% of local context | local, logs would-be `public:cloud_size` | **cloud default** |

Refusals are raised as LiteLLM `HTTPException`s with status 400 (private and too large) or 503
(laptop or configuration) and the fixed message; no request content is echoed.

## Logging

One JSON line per `auto` request on stdout (collected by the existing Vector pipeline):
`{"event": "hearth_router.decision", "mode", "reason", "would_route", "routed", "classified": <n>,
"cache_hits": <n>, "classifier_ms", "tokens_estimate", "request_id"}`. `reason` is one code from
the tables above; `would_route` and `routed` are `local`, `cloud` or `refuse`. No content, no
matched term, no key, no user. A test feeds requests containing marker strings and asserts none
appear in the log.

## What must not change

- **Every name except `auto` is untouched.** A test sends each catalogue name through the hook
  and asserts `data` is returned identical, without the messages being read.
- **P1a's catalogue test** stops forbidding `litellm_settings.callbacks` and instead requires it to
  equal exactly `["hearth_router.proxy_handler"]` when the router is enabled, and to be absent
  otherwise. `auto` is added to the lane entries the test recognises; it must not appear in
  `localInference.models`, and no alias, fallback or key metadata may map any name to it.
- **P1d's runtime lane test** gains a scenario with the hook loaded (its `LaneScenario` extension
  point): every local name **and `auto`**, through all three endpoints, streaming and not, with the
  laptop down, produces zero recorder requests and only gate attempts. For `auto` it also asserts
  the refusal message. **No router package merges without it.** A second scenario in enforce mode,
  with a fake classifier that answers `PUBLIC` and a long public prompt, must show exactly one
  recorder request to the cloud default and nothing else; the same with any rule marker present
  must show zero.
- **Private personas never use `auto`**, and coding-agent keys are not given it (D6). The runbook
  states both; the owner sets key allowlists.

## Packages

Appended to the orchestration brief once this spec is approved.

| ID | Package | Repo | Depends on |
|---|---|---|---|
| R1 | `service/hearth_router/`: decision engine (rules, classifier client, capability, mode), the LiteLLM `CustomLogger` adapter, unit tests for every rule row and every table row × three request formats, log-content test | hearthai | G5 |
| R2 | Chart, behind `router.enabled` (default false; requires `localInference.enabled`): module and config ConfigMap mounted next to `config.yaml`, term-list Secret mount (`router.termListSecret`), `callbacks` entry, `auto` catalogue entry, `router.mode`, `router.cloudDefault` validated; P1a test change; P1d lane scenarios (shadow and enforce); `docs/runbooks/auto-router.md` | hearthai | R1 |
| R3 | home-ops: term-list `ExternalSecret`; enable `router` in shadow mode | home-ops | R2 merged; owner creates the Bitwarden item |
| R4 | I6: `async_post_call_response_headers_hook` adds `x-hearth-route` and the actual model; runbook section for enforce mode | hearthai | R2 |
| R5 | home-ops: `router.mode: enforce` | home-ops | owner's shadow week reviewed |

## Owner steps

- Bitwarden item with the term list (one per line). R3 names it.
- Give `auto` only to the keys that should have it (Open WebUI's); not to coding-agent keys.
- Shadow week: use `auto`, read the `hearth_router.decision` lines in VictoriaLogs, note every
  `would_route: cloud` that should have stayed home, and the classifier latency.

## Verify before relying on it

These are read from source but must be confirmed by the R2 lane test against the running image:
`async_pre_call_hook` runs for `/v1/responses` and `/v1/messages` as well as Chat Completions;
a modified `data["model"]` is honoured by routing; and the key's model access is not re-checked
against the rewritten name (if it is, the Open WebUI key needs the cloud default too, which it
already has).
