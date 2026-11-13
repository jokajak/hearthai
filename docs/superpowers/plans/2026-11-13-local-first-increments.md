# Local-first incremental development plan

**Status:** proposed, for review. Nothing here is implemented; no chart, service or manifest changed.
**Date:** 2026-11-13.
**Supersedes:** the delivery order in [`ROADMAP.md`](../../ROADMAP.md) "Current priority correction — 2026-09-08" (draft replacement text in [Roadmap correction](#roadmap-correction-draft-not-applied)).
**Companion:** home-ops wiring, cutover order and owner steps are in
`home-ops/docs/plans/2026-11-13-hearthai-local-inference.md`.
**Unchanged:** the `ai-jobs` execution-substrate rules in [`ARCHITECTURE.md`](../../ARCHITECTURE.md), the
[webfetch design](../specs/2026-09-12-isolated-webfetch-design.md), and the deployment trust model.

This is a sequencing plan. Each increment is one small pull request (or one owner sitting) that
leaves the deployment working. Increments that need a design of their own say so and get a spec
before code.

## Priorities this plan follows

1. Opportunistic local inference on the owner's laptop: private personas, then an automatic router.
2. OpenAI and Anthropic models through the existing subscriptions only. No API keys.
3. The gateway: keep or replace, decided on what serves 1, 4 and 6.
4. Coding agents (Claude Code, Codex CLI, oh-my-pi) using the local model through the gateway.
5. Open WebUI as a chat surface with no tool use.
6. Sandboxed tool use for the web UI on the `ai-jobs` design.

## Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | **Keep LiteLLM as the gateway.** | It is the only candidate that already carries both subscription routes (`chatgpt/` device-code login, Meridian), and it serves all three wire APIs the coding CLIs speak (Chat Completions, Responses, Anthropic Messages) against any backend. It has per-key model allowlists, which this plan uses as the consumer-side privacy control, and a supported pre-call hook for the router. Alternatives (Bifrost, Portkey, Envoy AI Gateway, pointing clients at Ollama directly) would each lose the subscription routes or the virtual keys, and none serves priorities 1, 4 or 6 better. |
| D2 | **Keep Open WebUI as the chat surface.** Do not replace it with Mindroom or n8n. | Mindroom's execution model is persistent workers with shell, Python and file tools and default egress, which is the opposite of priorities 5 and 6, and its live config is dashboard-owned rather than Git-owned. n8n is a workflow tool, not a chat surface. Both stay as consumers. |
| D3 | **The privacy rule lives in the gateway model name, not in the persona.** A *lane* is a gateway model name with fixed routing: `local-qwen` (laptop only), the existing cloud names, and later `auto` (router). A persona is a system prompt bound to one lane. | Every consumer that binds to `local-qwen` inherits "laptop or nothing" without implementing anything. A persona cannot weaken its lane. |
| D4 | **Private personas reach the laptop through a small in-cluster gate, not directly from LiteLLM.** | LiteLLM passes one timeout for connect and read, so it cannot tell "host gone" (should fail in seconds) from "model loading a long prompt" (may take a minute). The gate gives a 3-second dial timeout, an active probe that does not load the model, one fixed error message, a probe target for the status page, and a single place where the laptop address lives. |
| D5 | **"Unavailable" means: still listed, fails in about 3 seconds with a fixed message, and shown as down on the status page.** | Open WebUI has no per-model health badge and its extension points (functions) are disabled under priority 5. The picker therefore cannot grey a model out. See open decision O2. |
| D6 | **The automatic router is a separate lane (`auto`) and is not offered to private personas or to coding-agent keys.** | Requirement. A private persona is bound to `local-qwen`, which has one backend and no router in front of it. |
| D7 | **First tool exposed to the web UI: shared-memory recall. Webfetch is third, behind a lane gate.** | An egress tool is an exfiltration channel for private chats, and Open WebUI lets a user switch any accessible tool on in any chat. Git change work is covered by priority 4. See [First tool](#first-tool-comparison). |

### What "private" promises, and what it does not

A private persona's requests go: Open WebUI → LiteLLM (`local-qwen`) → gate → Ollama on the laptop.
They never leave the household's machines. They are not confined to the laptop: the conversation is
stored in Open WebUI's database in the cluster, LiteLLM records token counts, and the request crosses
the LAN as plain HTTP. If "private" should mean "never leaves the laptop", that is a different
product (Open WebUI on the laptop) and is not planned here.

The guarantee is enforced by configuration that tests pin, not by network topology. LiteLLM holds
cloud credentials in the same process. If the owner wants a topological guarantee, the upgrade is a
second, credential-free LiteLLM instance for the local lane (open decision O1).

Leak paths this plan closes, each with a test or an owner check:

| Path | Closed by |
|---|---|
| LiteLLM fallback, alias or load-balancing onto a cloud deployment | I1 chart tests: one deployment per local name, no fallback or alias keys, no wildcard routes |
| A cloud deployment added under the same name through the LiteLLM admin UI | I4: `store_model_in_db` off, so the catalogue is the chart file only |
| Open WebUI title, tag, follow-up and autocomplete generation sent to a separate "task model" | I1 test: `TASK_MODEL` and `TASK_MODEL_EXTERNAL` stay empty, so tasks use the chat's own model |
| Open WebUI arena model (answers from a randomly chosen model) | I3: disabled |
| Open WebUI memory written in a private chat and injected into a later cloud chat | I2 owner step: memory capability off on private personas; O6 |
| A coding CLI's background model (titles, summaries) defaulting to a cloud name | I7: local-only virtual key; the gateway refuses any other model for that key |
| Mindroom's router model reading a private room | I9: verify, otherwise no private personas in Mindroom |
| A tool with egress called from a private chat | D7 and I15: no egress tool is reachable from the local lane |

## Practical limits of the laptop

**Memory against context (36 GB unified).** A 35B model at 4-bit quantisation is roughly 21–23 GB of
weights. macOS lets Metal wire roughly two thirds to three quarters of unified memory, so about
24–27 GB is usable before the model spills to CPU or the system swaps. The KV cache grows linearly
with context; for a model of this shape expect on the order of 3 GB at 32k tokens and 6 GB at 64k at
f16, about half that with a quantised cache. These are estimates from the architecture family.

**Measured 2026-11-13:** `ollama ps` reports 23 GB, `100% GPU`, context 32768, runner `mlx`. 32k
fits with about 13 GB left for the desktop. At 65536 the report is identical: 23 GB, `100% GPU`.
The size not moving suggests the MLX runner grows its cache as the prompt grows instead of
reserving it at load, so this shows 64k loads, not that a full 64k prompt fits. A long-prompt run
confirmed it: mid-run the `ollama` process was at 27 GB, wired memory 28.8 GB of 36, swap in use
9.8 GB, and memory pressure went yellow and red with a normal desktop open (a browser with many
tabs). The prompt was 41,760 tokens and took 66 s to evaluate cold (about 630 tokens/s), so the
cache costs at least 0.1 GB per 1,000 tokens. After the run the process fell back to 23 GB, wired
memory to 25 GB, and swap stayed at 8 GB: the loaded model plus the desktop already exceeds 36 GB
before any context is used. The prompt cache does survive between requests: the identical prompt
re-evaluated in 0.2 s instead of 66 s. Untested: whether a different conversation in between
evicts it, which decides how chat and a coding agent interfere (see I7). **64k does not fit beside ordinary desktop use.** Advertise 32k; treat 64k as something to
use only with the desktop mostly closed. The runner is MLX, not llama.cpp, so the two cache
settings below may have no effect.

| Setting on the laptop | Value | Why |
|---|---|---|
| `OLLAMA_CONTEXT_LENGTH` | 32768 to start | Fits with headroom for the desktop. One value for all clients: Ollama reloads the model when a request asks for a different context, which costs 10–20 s and drops the prompt cache. |
| `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` | on, if the runner honours them | Halves cache memory on the llama.cpp runner. Unconfirmed on MLX. |
| `OLLAMA_NUM_PARALLEL` | 1 | Each parallel slot allocates its own context. |
| `OLLAMA_MAX_LOADED_MODELS` | 1 | A second loaded model does not fit. |
| `OLLAMA_KEEP_ALIVE` | leave at the 5m default | Measured: with the model merely loaded and idle, the laptop sits at 8 GB of swap beside a normal desktop. A longer keep-alive holds it there. Raise it per coding session, not globally. |

Measure after setup: `ollama ps` must show `100% GPU` at the chosen context with normal desktop apps
open. If it shows a CPU split, lower the context. Try 64k only after 32k is stable, because Claude
Code needs it (see I7). The gateway never sends `num_ctx`; it advertises the laptop's value as
`max_input_tokens` so clients compact before Ollama silently truncates.

**Ollama listens on localhost.** The macOS app ignores shell exports. Either use the app's network
exposure setting or `launchctl setenv OLLAMA_HOST "0.0.0.0:11434"` and restart the app; `launchctl`
values do not survive a reboot unless a LaunchAgent sets them. Ollama has no authentication, so once
exposed, any host that can reach the laptop on that port can use the model, including on networks
away from home. Restricting that (pf rule, or enabling exposure only at home) is the owner's network
decision; the plan only records that it is open.

**Detecting that the laptop is gone.** The gate probes `GET /api/version` (does not load the model)
every 10 s with a 2 s timeout, and dials with a 3 s timeout. An outage is noticed within about 12 s;
a request inside that window fails in 3 s. Recovery is noticed within 10 s. LiteLLM's own background
health check is disabled for the local model: it works by sending a real completion, which would
load 22 GB into memory every five minutes.

**The address.** The gate reads the laptop URL from one environment value. home-ops supplies it as
`http://${SECRET_LOCAL_LLM_HOST}:11434`. The address appears in no chart file, test or document.
The laptop needs a stable address or name on the home network (DHCP reservation or local DNS).

**One request at a time.** Chat and a coding agent queue behind each other. Acceptable; noted so it
is not mistaken for a fault.

## Milestone 1 — a private persona on the laptop (north star)

Two increments. Priority 2 needs nothing here (the subscription routes are untouched) and
priority 3 is settled by D1.

### I1. Local lane and gate in the chart, off by default

**Purpose:** the chart can route one model name to a laptop and nowhere else.
**Repo:** hearthai (chart only). **Deploys as a no-op:** home-ops floats on `main`, so this merge
rolls nothing while `localInference.enabled` is false.

**Files:** `deploy/charts/hearthai/values.yaml`, `values.schema.json`,
`templates/litellm-configmap.yaml`, new `templates/local-gate.yaml`, `templates/NOTES.txt`,
`deploy/tests/test_hearthai_chart.py`, `deploy/README.md`, `deploy/examples/hearthai-values.yaml`,
`.github/renovate.json5` (gate image pin).

- [ ] Add `localInference.enabled` (default false), `localInference.upstreamUrl` (required when
  enabled, never defaulted), and `localInference.models[]` with `name`, `ollamaModel`,
  `contextWindow`. Default list: `local-qwen` → `qwen3.6:35b-a3b`, 32768.
- [ ] Render the gate: one Deployment, Service and ConfigMap (`<release>-local`). A pinned stock
  Caddy image with a short Caddyfile: reverse proxy to the upstream, 3 s dial timeout, active health
  check on `/api/version`, streaming unbuffered, and every upstream failure mapped to one `503` JSON
  body: *"Local model unavailable: the laptop is off, asleep or off the network. This model never
  falls back to another provider."* No custom code.
- [ ] Gate network policy, same pattern as Meridian: ingress from this release's LiteLLM pods and
  node probes only. The gate mounts no Secret and no service-account token.
- [ ] When enabled, append one LiteLLM entry per model: `ollama_chat/<ollamaModel>`, `api_base` the
  gate Service, `max_input_tokens` from `contextWindow`, `supports_function_calling: true`,
  `disable_background_health_check: true`. The bundled catalogue file stays byte-identical; the
  entries are appended at render time, so the existing SHA-256 test keeps passing.
- [ ] Document the lane in `deploy/README.md`: what it guarantees, what it does not, and the laptop
  settings table above.

**Tests (no cluster):** `helm lint`, `pytest deploy/tests`, kubeconform in CI.

- [ ] Default render is unchanged from today (no gate objects, catalogue hash unchanged).
- [ ] Enabled without `upstreamUrl` fails to render.
- [ ] Every local model name occurs exactly once in `model_list`, and its `api_base` is the gate.
- [ ] The rendered config contains no `fallbacks`, `default_fallbacks`, `context_window_fallbacks`,
  `content_policy_fallbacks`, `model_group_alias`, and no model name containing `*`.
- [ ] No `litellm_params` in the catalogue carries a provider API key other than
  `MERIDIAN_API_KEY` (priority 2: subscriptions only).
- [ ] The Open WebUI ConfigMap has no non-empty `TASK_MODEL` or `TASK_MODEL_EXTERNAL`.
- [ ] The upstream URL appears only in the gate's environment, not in any ConfigMap.

**Owner check once live:** the release reconciles and nothing restarted except by checksum. No new
model in the picker. That is the expected result.

### I2. Turn it on, set up the laptop, create the first private persona

**Purpose:** the north star.
**Repo:** home-ops wiring (see companion), plus owner steps. No hearthai change.

**Owner steps, in order:**

1. Laptop: pull `qwen3.6:35b-a3b`; apply the settings table; expose Ollama to the network; give the
   laptop a stable address; confirm `ollama ps` shows `100% GPU` after one prompt.
2. home-ops: populate `SECRET_LOCAL_LLM_HOST` in `cluster-secrets` (name or address, no scheme or
   port).
3. Merge the home-ops change that sets `localInference.enabled: true` and adds the status probe.
4. LiteLLM admin UI: confirm Open WebUI's virtual key can see `local-qwen`. A key with no model
   restriction sees it automatically.
5. Open WebUI, Admin → Models: create the persona. Base model `local-qwen`; a system prompt; a name
   that states the rule, for example "Journal (local only)"; memory and built-in tools off for this
   preset; visible to the intended users. This is a one-time UI step per persona. I11 moves personas
   into code.

**Owner checks once live:**

- [ ] Laptop awake: the persona answers; `ollama ps` on the laptop shows the model loaded; LiteLLM's
  request log shows `local-qwen` and no other model for that chat, including its title request.
- [ ] Laptop asleep or off the network: the persona is still in the picker; a message fails within
  about 3 s with the fixed text; the status page shows the local-inference tile down; no request for
  any other model appears in LiteLLM's log.
- [ ] Laptop back: the next message succeeds within about 10 s without touching the cluster.

**Acceptance for the milestone:** both states behave as above on two consecutive days, and no cloud
route was used by the private persona at any point.

## Milestone 2 — the surface has no tools, and the catalogue is code

### I3. Open WebUI deny table

**Purpose:** priority 5. Every model-callable or user-installable execution path in Open WebUI is
off by configuration, and a test says so.
**Repo:** hearthai (chart). Rolls the Open WebUI pod once.

**Files:** `templates/configmap.yaml`, `deploy/tests/test_hearthai_chart.py`, new
`deploy/tests/check_openwebui_env.py`, `.github/workflows/ci.yaml`, `deploy/README.md`.

Risk assessment and the setting that closes each. Already set today: the first four rows.

| Surface | Risk | Setting (value) |
|---|---|---|
| Code execution, code interpreter | Model-written code runs in the browser or in the root-running Open WebUI pod, next to the session key, the gateway key and the OIDC secret | `ENABLE_CODE_EXECUTION`, `ENABLE_CODE_INTERPRETER` (`false`); add `USER_PERMISSIONS_FEATURES_CODE_INTERPRETER` (`false`) |
| Workspace tools | In-process Python with the pod's network and secrets | `USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS`, `..._IMPORT` (`false`) |
| Direct Ollama connection | A second, unkeyed model path that bypasses the gateway and its lanes | `ENABLE_OLLAMA_API` (`false`) |
| UI-edited settings | Any of this table re-enabled from the admin panel and persisted | `ENABLE_PERSISTENT_CONFIG` (`false`) |
| Web search and page loading | Egress chosen by the model; exfiltration channel for private chats; fetches run from the pod | `ENABLE_WEB_SEARCH`, `USER_PERMISSIONS_FEATURES_WEB_SEARCH` (`false`) |
| Image generation | Sends prompt text to another backend | `ENABLE_IMAGE_GENERATION`, `USER_PERMISSIONS_FEATURES_IMAGE_GENERATION` (`false`) |
| External tool servers (OpenAPI, MCP), global and per-user | Arbitrary remote tools, including ones a user adds from the browser | `TOOL_SERVER_CONNECTIONS` (`[]`), `ENABLE_DIRECT_CONNECTIONS`, `USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS` (`false`) |
| Terminal integration | A general shell | terminal server connections empty (name to confirm) |
| Scheduled automations, channels, notes agents | Unattended runs with tool approval | `USER_PERMISSIONS_FEATURES_AUTOMATIONS`, `ENABLE_CHANNELS` (`false`) |
| Arena models | A random model answers; breaks lane choice | `ENABLE_EVALUATION_ARENA_MODELS` (`false`) |
| Community sharing | Chats uploaded to a third party | `ENABLE_COMMUNITY_SHARING` (`false`) |
| Functions, pipes, filters, actions | In-process Python installed by an administrator | **No setting disables these for administrators.** Residual risk; see below |

- [ ] Add the missing rows to the ConfigMap. Confirm each name against the pinned Open WebUI source
  before writing it; a misspelt name is silently ignored and the upstream default applies.
- [ ] Chart test: the deny table is a literal in the test. It fails if a key is missing or has
  another value.
- [ ] Drift check in CI: `check_openwebui_env.py` reads the pinned tag from `values.yaml`, fetches
  that tag's `backend/open_webui/config.py` and `env.py`, and fails if any deny-table name is absent.
  This is what catches a rename when Renovate bumps Open WebUI.
- [ ] `deploy/README.md`: the residual risk, stated plainly. An administrator can install a
  function; functions run as root in the pod. The control is that none is installed and the
  administrator account is not used to add one. Sandboxed tools come from M6, not from here.

**Verify without cluster access:** `helm template` plus the chart test proves the settings are
rendered; the drift check proves the pinned version reads them.
**Owner check once live:** the chat composer's integrations menu offers no web search, code
interpreter, image or tool entries; Admin → Functions and Workspace → Tools are empty;
`GET <url>/api/config` reports the features as disabled.

Two things outside Open WebUI to confirm once, because they are the same class of risk:

- [ ] Meridian wraps Claude Code. Confirm from its source at the pinned version that it does not
  execute Claude Code's built-in tools (shell, web fetch) inside the Meridian pod for proxied
  requests. If it can, pin the setting that disables it in the chart.
- [ ] The `chatgpt/` route accepts OpenAI-hosted tools (web search) if a client asks for them. Open
  WebUI does not. Record it in `deploy/README.md` as a property of the route.

### I4. The catalogue is the chart file only

**Purpose:** remove the one way a cloud deployment could be attached to a local name outside Git.
**Repo:** hearthai (chart), after an owner pre-check. **Order matters.**

- [ ] Owner pre-check, before merge: in the LiteLLM admin UI, list models and note any that exist
  only in the database. If one is in use, add it to `files/litellm-config.yaml` first.
- [ ] Set `store_model_in_db: false` and the matching deployment environment value. Virtual keys,
  budgets and spend stay in Postgres and are unaffected.
- [ ] Update the catalogue hash test and the README note that calls the file a byte-for-byte import;
  from here the file is HearthAI's catalogue.

**Owner check once live:** all existing keys still work; the model list in the UI equals the chart
file.

## Milestone 3 — the automatic router

Needs its own spec before code (`docs/superpowers/specs/`). The design below is the brief for it.

**Entry point:** a gateway model name, `auto`. It appears in the picker beside the lanes. It is
implemented as a LiteLLM pre-call hook (`litellm_settings.callbacks`), a Python module shipped in a
chart ConfigMap and unit-tested in this repository. It runs before the model is chosen and can only
set the target to one of two configured names or raise a refusal.

**How it decides.** The whole message list is inspected on every request, so one private turn keeps
the conversation local for as long as that turn is in the history. No state is kept.

1. **Rules (deterministic, no model).** Credential patterns, account and identity numbers, an
   owner-supplied term list (names, places, employers; delivered as a Secret because the list is
   itself sensitive), any attachment or image, and an explicit `#private` marker. Any match: local.
2. **Local classifier.** If the rules are clean, the laptop model is asked one constrained question
   about the new content: does it contain personal, household, health, financial, legal or
   credential information? The only answer that permits cloud is the exact token `PUBLIC`. Verdicts
   are cached in memory by content hash.
3. **Capability.** Content judged public goes to the local model unless it exceeds the advertised
   local context or asks for something the hook's fixed heuristics send to the configured cloud
   default. Local is preferred whenever it can do the job.

**Fail-closed table** (this table becomes the unit tests):

| Situation | Result |
|---|---|
| Rule match | local |
| Classifier says anything other than `PUBLIC`, times out, errors, or returns malformed output | local |
| Hook raises, term list missing, configuration invalid | refuse |
| Private content larger than the local context | refuse, with "too large for the local model" |
| Laptop offline | refuse: "Automatic routing needs the laptop. Choose a cloud model yourself if this is not private." |
| Rules clean, classifier `PUBLIC`, beyond local capability | cloud default |

**Laptop offline means `auto` refuses everything.** Without the laptop there is no way to establish
that content is public, and the rule is local-or-refuse when unsure. Picking a cloud model by hand
remains available. A rules-only offline mode would send regex-clean private text to the cloud and is
rejected (O4).

**What the guarantee is.** Cloud is reachable from `auto` only through one code path that requires
clean rules and an explicit `PUBLIC` verdict; every error path ends local or refused, and tests
inject each failure. It is not a guarantee that the classifier is right. A local model can misjudge
private text as public. That is why private personas do not use `auto`, and why I5 ships in shadow
mode first. LiteLLM's built-in semantic auto-router is not used: it needs an embedding call and
routes by similarity, which is not fail-closed.

### I5. Router hook in shadow mode

**Repo:** hearthai (chart + module + unit tests); home-ops (term-list Secret, see companion).
**Files:** new `service/hearth_router/` (module and tests), chart ConfigMap and mount in
`templates/litellm.yaml`, `auto` catalogue entry, `deploy/tests`, `.github/workflows/ci.yaml`.

- [ ] `auto` always resolves to `local-qwen` or refuses. The hook logs the decision it *would* have
  made, as a reason code with no content.
- [ ] Unit tests for every row of the fail-closed table, against Chat Completions, Responses and
  Anthropic Messages request shapes.
- [ ] Chart test: `auto` is not in `localInference.models`, and no key metadata or alias maps a
  private name to it.

**Owner check once live:** use `auto` for a week; read the reason codes; note every case where the
would-be decision was cloud for something that should have stayed home.

### I6. Enable the cloud branch

- [ ] One value turns shadow mode off. The response carries an `x-hearth-route` header and the
  actual model name.
- [ ] Runbook `docs/runbooks/auto-router.md`: the decision table, how to read the log, how to add
  terms, how to turn the cloud branch off.

**Owner check once live:** a public long-context question goes to the cloud default; a message with
a listed term goes local; with the laptop asleep, `auto` refuses and names the alternative.

## Milestone 4 — coding agents through the gateway

Independent of M2 and M3; can be done straight after M1.

| Tool | API it speaks | Gateway route | Notes with the local model |
|---|---|---|---|
| Claude Code | Anthropic Messages | `/v1/messages` (LiteLLM translates to Ollama; upstream marks the route beta) | About 15–25k tokens of system prompt and tool schemas before any work, so 32k leaves little room; 64k is the practical minimum. Sets several model names (main, small/fast, subagent); all must be `local-qwen`. Calls a token-count endpoint; confirm the gateway answers it for a non-Anthropic model. |
| Codex CLI | OpenAI Responses | `/v1/responses` (LiteLLM bridges to the model's chat API) | Custom provider with `wire_api = "responses"`. Lighter prompt; 32k is workable. Set the context window and auto-compact limit to the advertised value. |
| oh-my-pi | Chat Completions (also supports the other two) | `/v1/chat/completions` | Custom provider entry. Simplest path; use it as the reference when the other two misbehave. |

Tool calling goes through in all three: the CLI sends tool schemas, the gateway translates them, the
model returns calls, and the tools run on the laptop where the CLI is. Expect weaker results than
with hosted models when many tools are offered at once, and occasional malformed calls. The laptop
serves one request at a time.

**What the gateway's auth has to provide** (network reach is the owner's):

- one virtual key per tool or per machine, never the master key;
- a model allowlist per key, so an agent key lists `local-qwen` only and a request for any other
  name is refused. This is what stops a CLI's background model from reaching a cloud route;
- a rate or parallel-request limit per key, and revocation without affecting other consumers.

The same key works from anywhere the gateway hostname is reachable. Nothing else is needed for
remote use.

### I7. Gateway readiness and the runbook

**Repo:** hearthai (docs, small chart change); home-ops (ingress timeouts, see companion).
**Files:** new `docs/runbooks/coding-agents.md`, `files/litellm-config.yaml` or the local entries
(context metadata, pre-call context check), `deploy/README.md`.

- [ ] Runbook with one section per tool: the exact settings, which names must point at
  `local-qwen`, the context numbers, how to mint the local-only key, and a three-step smoke test
  (read a file, edit it, run a command).
- [ ] Enable LiteLLM's pre-call context check for the local entries so an oversized request is
  refused with a context error instead of being truncated by Ollama.
- [ ] Decide the laptop context (O3) and set `contextWindow` to match.

**Verify without cluster access:** chart tests for the metadata; the runbook's commands are
copy-paste checked against each tool's current documentation.
**Owner check once live:** each CLI completes the smoke test; LiteLLM's log shows only `local-qwen`
for the agent key; asking the agent key for a cloud model is refused; with the laptop asleep each
CLI shows the gate's message.

### I8. Compatibility fixes found by the smoke tests

Reserved. Likely candidates: parameters the Ollama route rejects (handled by `drop_params`), the
token-count endpoint, and streaming tool-call shape. One fix per pull request, each with a
regression test or a runbook note.

## Milestone 5 — the same personas for every consumer

| Consumer | What it needs | Privacy and availability |
|---|---|---|
| Open WebUI | Nothing after I2. After I11, no presets at all: personas arrive as gateway models. | As M1. |
| Mindroom | Its virtual key's allowlist extended to the lanes it should see. A `models:` entry per lane pointing at the gateway, and an agent bound to it. Its live config is dashboard-owned, so this is an owner step; update the Git seed to match for rebuilds. | An agent on `local-qwen` must have no tools (its workers have default egress). **Mindroom's router model is a cloud model and reads room messages.** Unless the router can be local or bypassed for a room, private personas must not be offered in Mindroom. Unavailability shows as the gate's error in the room. |
| n8n | Its credential already points at the gateway; the model list shows the lanes. For private workflows, a second credential holding a local-only key. | Never enable a fallback model or an error branch that retries on a cloud model in a private workflow. With the local-only key the gateway refuses it anyway. Unavailability is a failed node; handle it on the error branch. |

### I9. Mindroom on the lanes
Owner steps and a seed-config update in home-ops (companion). First verify the router-model
question above against the pinned Mindroom version; record the answer in the Mindroom README.

### I10. n8n on the lanes
Owner steps only: mint the local-only key, add the second credential, note the no-fallback rule in
the home-ops n8n notes.

### I11. Personas as code at the gateway
**Purpose:** one definition of each persona, visible to all three consumers as a model name.
**Repo:** hearthai (reuses the hook from I5); home-ops (the persona list in release values).

- [ ] `personas[]` in values: `name`, `lane`, `prompt`. The hook prepends the prompt and sets the
  target to the lane. A persona on `local-qwen` gets the lane's guarantee; the hook cannot route a
  persona anywhere but its declared lane (test).
- [ ] Open WebUI presets created in I2 are retired by hand once their gateway equivalents work.

This changes where a persona is defined (O5). Until it ships, Mindroom and n8n repeat the prompt
text in their own configuration.

## Milestone 6 — sandboxed tools for the web UI

Each tool needs the lane it is called from to be compatible with what the tool can reach. The
`ai-jobs` rules stand: typed request, fixed profile, ephemeral pods, no service-account token, no
ambient credentials, separate publisher for writes, durable audit.

### First tool comparison

| Candidate | State | Egress | Credentials | Value on day one | Verdict |
|---|---|---|---|---|---|
| Shared-memory recall | Service built, no consumer, no tool definition | hearthmem Service only | Store capability, delivered per run | Household context in any chat, including private ones. Needs a seeded store. | **First.** Cannot leak a private chat; simplest possible profile (one pod, one in-cluster destination), so it validates the executor without also validating a handoff. |
| Shared-memory write | Service built | hearthmem Service only | Write capability in the publisher only | Makes recall useful without the CLI | **Second.** First use of approval and the separate publisher. |
| Webfetch | Both stages, contracts and rules built; no executor | Public internet | None | Highest. Current information in chat. | **Third, cloud lanes only.** The model chooses the URL, so a private chat can be exfiltrated through it, and Open WebUI lets a user enable any accessible tool in any chat. Needs the lane gate (I15) first. |
| Web research | Contract only; depends on webfetch | Search engines and the internet | Search and model access, brokered | High | After webfetch. Same lane rule; queries leak too. |
| Git change | Contract only | GitHub | App key, materialiser and publisher tokens | Covered by priority 4 on the laptop | **Deferred.** The most machinery for the least new value now. Keep the contract. |
| General code execution | Not designed | None | None | Moderate | Excluded by the standing architecture decision. Not reopened. |

Recall is a weak test of isolation, because nothing untrusted is parsed in the pod. That is accepted:
the executor's first job is lifecycle, admission, audit and the envelope. Isolation is proven when
webfetch lands on an executor that already works.

### I12. Executor skeleton and the sandbox namespace
Needs a spec. **Repo:** hearthai (`service/ai_jobs` executor, image, chart templates behind
`aiJobs.enabled`); home-ops (namespace, quota, default-deny policy, RBAC inputs; must be live
first, see companion).
One fixed no-op profile: create one pod, wait, collect one envelope, delete, write the audit record.
Tests: unit tests against a fake Kubernetes API; chart tests that the pod spec has no token, no
host mounts, read-only root, and that no request field reaches the pod spec.
Not exposed to Open WebUI.

### I13. Memory recall, exposed
`memory_recall` tool definition, the `memory-recall-v1` profile, and the Open WebUI registration as
one entry in `TOOL_SERVER_CONNECTIONS` (the only non-empty value the I3 deny table then allows; the
test changes with it). Owner steps: create the first store, put its token in Bitwarden, seed a few
records with the CLI. Rule until lane-aware admission exists: a shared store holds nothing that may
not reach a cloud model, because a cloud chat can recall it.

### I14. Memory write through the publisher
Needs a spec for approval. The model proposes exact content and destination; a person approves
outside the model's reach (O7); a publisher pod holding the write capability commits it.

### I15. Lane gate, then webfetch
Spike first: whether the pinned Open WebUI can bind a tool to specific model presets so it cannot be
enabled elsewhere. If it can, declare that and test it. If not, `ai-jobs` admits an egress tool only
with a chat identifier that the gateway hook attests has used cloud lanes only, and refuses when the
identifier is missing. Then complete steps 4 and 5 of the
[webfetch plan](2026-09-12-isolated-webfetch.md) on the executor from I12.

## Order that matters across repositories

home-ops follows `hearthai` `main`, so every chart merge deploys. The rules for this plan:

1. A chart change that adds a capability ships default-off or compatible with current home-ops
   values. Never add a `required` value to a path home-ops already renders.
2. The chart merge reconciles cleanly before the home-ops change that turns the capability on.
3. Anything read from the live cluster rather than Git (the laptop address in `cluster-secrets`,
   the sandbox namespace and its default-deny policy, database-only LiteLLM models) is in place and
   confirmed by the owner before the change that depends on it.

The companion document gives the concrete sequence for I2, I4, I5 and I12.

## Open decisions

| # | Question | Recommendation |
|---|---|---|
| O1 | Is a tested configuration guarantee enough for the private lane, or should it be a separate credential-free LiteLLM instance with egress limited to the gate? | Start with the tested configuration (I1, I4). Add the second instance only if a review of live behaviour finds a path the tests cannot pin. It costs a second deployment and a second key set. |
| O2 | Is "listed, fails fast with a fixed message, down on the status page" acceptable as *unavailable*? | Yes. A badge in the picker needs an Open WebUI extension, which priority 5 rules out. Put the rule in the persona name. |
| O3 | Laptop context: 32k or 64k? | 32k for M1. Move to 64k with the quantised cache before I7 if `ollama ps` stays at 100% GPU; Claude Code is not practical below it. |
| O4 | Should `auto` have a rules-only mode when the laptop is offline? | No. It contradicts local-or-refuse. |
| O5 | Where is a persona defined: an Open WebUI preset (today's definition) or a gateway model (I11)? | Gateway, after M3. It is the only way Mindroom and n8n see the same list. Keep presets until then. |
| O6 | Open WebUI personal memory and private personas. | Off for private personas now. A memory written from a private chat is injected into later chats on any model. Revisit with I14. |
| O7 | How does a person approve a shared write from Open WebUI? | An approval page served by `ai-jobs` behind the existing identity provider, linked from the tool result. A second model-callable "confirm" tool is not approval. |
| O8 | Should `auto` be offered to Mindroom and n8n? | Not until I6 has run for a month in Open WebUI. |
| O9 | Pin home-ops to a hearthai commit instead of floating on `main`? | Pin once M1 is live. From M2 on, chart merges change security settings and should not deploy unreviewed. |
| O10 | A persona's prompt may itself be private. Keep it out of Git? | If it matters, define that persona as an Ollama Modelfile on the laptop and list the resulting model under `localInference.models`. The prompt then never leaves the laptop's disk. |

## Verify before relying on it

Stated from documentation or memory and not checked against the pinned versions in this plan.

| Claim | Where to check | Blocks |
|---|---|---|
| Open WebUI setting names in the I3 table, at the pinned tag | upstream `config.py`, `env.py` | I3 |
| `disable_background_health_check` per model; the pre-call hook signature; `store_model_in_db: false` leaves keys working | LiteLLM docs and source at the pinned tag | I1, I4, I5 |
| `/v1/messages` and `/v1/responses` against an `ollama_chat` model, with tools and streaming; token counting | live smoke test | I7 |
| Memory with a *full* 64k prompt for `qwen3.6:35b-a3b` (loads at 23 GB, 100% GPU at both 32k and 64k); whether the cache settings apply to the MLX runner | long prompt on the laptop, watching memory pressure and swap | O3, I7 |
| oh-my-pi provider configuration format | its documentation | I7 |
| Meridian does not run Claude Code's built-in tools | its source at the pinned tag | I3 |
| Mindroom router model scope | its configuration reference | I9 |
| Whether Open WebUI can scope a tool server to a model preset | upstream source | I15 |

## Roadmap correction (draft, not applied)

Insert in `docs/ROADMAP.md` above "Current priority correction — 2026-09-08", and change that
section's first sentence to say it is superseded by this one.

> ## Current priority correction — 2026-11-13
>
> This section supersedes the delivery order in the 2026-09-08 correction. The execution-substrate
> rules in that correction are unchanged. Plan:
> [`superpowers/plans/2026-11-13-local-first-increments.md`](superpowers/plans/2026-11-13-local-first-increments.md).
>
> 1. **Opportunistic local inference.** A model on the owner's laptop is served through the gateway
>    as a lane that has one backend and no fallback. Private personas bind to it and show as
>    unavailable when the laptop is unreachable. An automatic router is a separate lane that chooses
>    local or cloud per request and chooses local or refuses when unsure.
> 2. **Hosted models through the existing subscriptions only.** No provider API keys.
> 3. **LiteLLM remains the gateway and Open WebUI the chat surface.**
> 4. **Coding agents through the gateway.** Claude Code, Codex CLI and oh-my-pi use the local lane
>    with local-only virtual keys. This replaces Git change work as the first answer to GitHub
>    contribution; the Git change profile is deferred and its contract kept.
> 5. **Open WebUI has no tool use.** Built-in tools, functions, code execution and web access stay
>    disabled by configuration that tests pin.
> 6. **Sandboxed tools on `ai-jobs`,** in this order: shared-memory recall, shared-memory write
>    through the publisher, webfetch on cloud lanes only, then web research. No tool with egress is
>    reachable from the local lane.
>
> Automatic topic organization and governed MCP keep their place after these. The numbered
> `0.1`–`0.4` detail remains pending rewrite.

Also update when applying: the `ROADMAP.md` session-recovery item 3 (point at this section), the
README's "Current product priority" paragraph, and the `ARCHITECTURE.md` decision table (add the
lane rule; mark Git change as deferred, not first).
