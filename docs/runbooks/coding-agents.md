# Coding-agents runbook

**Status:** documentation only. Nothing here was run against the gateway or the
laptop from the authoring environment; each configuration below is quoted from the
tool's current documentation, and the chart tests check only the gateway side
(the context check and the 32768 limit). Run the smoke tests before relying on any
of it.

This is how Claude Code, Codex CLI and oh-my-pi (`omp`) on the owner's laptop use the
local model through the gateway, so one endpoint and one kind of key serve all three.
Placeholders used throughout: `https://llm.example.com` is the gateway,
`sk-REPLACE` is a virtual key. The model is `local-qwen`, served by the local lane
(`localInference`) and advertised with a 32768-token window.

## What to expect from the local model

Measured on the owner's laptop:

* Context is 32768 tokens.
* A cold prompt evaluates at roughly 630 tokens per second, so a 20k-token first
  turn waits about 30 seconds.
* An identical prefix is reused on the next request: 0.2 s instead of 66 s for
  42k tokens. Keep the start of the prompt stable.
* The cache is lost when the model unloads after Ollama's keep-alive. The first
  turn after a pause is cold again.
* One request is served at a time. A second tool, or a subagent, waits.
* With a full browser open the laptop swaps whenever the model is loaded. Close the
  browser or expect slow turns.

The gateway refuses a request whose prompt exceeds 32768 tokens
(`router_settings.enable_pre_call_checks`, see [Known limits](#known-limits)), so an
oversized prompt fails with a context error rather than being silently truncated.

## Which API each tool speaks

| Tool | Gateway route | Why |
|---|---|---|
| Claude Code | `POST /v1/messages` (Anthropic Messages) | Claude Code sends Anthropic format to `ANTHROPIC_BASE_URL`; LiteLLM bridges it to the model |
| Codex CLI | `POST /v1/responses` (Responses) | Codex documents `responses` as the only supported `wire_api` |
| oh-my-pi | `POST /v1/chat/completions` (Chat Completions) | provider `api: openai-completions` |

Responses and Messages requests reach an `ollama_chat/` model through LiteLLM's
translation. That bridging is the least exercised path; the smoke tests are how you
find out whether it holds for a given tool version.

## Claude Code

Docs: <https://code.claude.com/docs/en/llm-gateway-connect.md>,
<https://code.claude.com/docs/en/model-config.md>,
<https://code.claude.com/docs/en/env-vars.md>.

Claude Code calls the Anthropic Messages API at `$ANTHROPIC_BASE_URL/v1/messages`.
Put the virtual key in `ANTHROPIC_AUTH_TOKEN` (sent as `Authorization: Bearer`, which
LiteLLM accepts for virtual keys). Anthropic states it does not support routing
Claude Code to non-Claude models through a gateway; this is outside that support.

```bash
export ANTHROPIC_BASE_URL=https://llm.example.com
export ANTHROPIC_AUTH_TOKEN=sk-REPLACE

# Every model-name setting, all local-qwen:
export ANTHROPIC_MODEL=local-qwen
export ANTHROPIC_DEFAULT_OPUS_MODEL=local-qwen
export ANTHROPIC_DEFAULT_SONNET_MODEL=local-qwen
export ANTHROPIC_DEFAULT_HAIKU_MODEL=local-qwen
export ANTHROPIC_DEFAULT_FABLE_MODEL=local-qwen
export CLAUDE_CODE_SUBAGENT_MODEL=local-qwen
export ANTHROPIC_SMALL_FAST_MODEL=local-qwen   # deprecated, still read by older versions

# Context: tell Claude Code the real window.
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=32768
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

Or the same values in the `env` block of `~/.claude/settings.json` (never in a
project's committed `.claude/settings.json`).

Why every model name: Claude Code runs background work (titles, summaries) on the
`haiku` alias and subagents on their own model. Left at their defaults those
requests would go to the gateway under a cloud name such as a Claude model, which the
key must not be allowed to use (so they fail) or, with a broader key, would leave
the laptop.

Context settings. `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is documented to apply on its own
to an unrecognised model id that does not start with `claude-` and has no `[1m]`
suffix, which `local-qwen` is. `CLAUDE_CODE_AUTO_COMPACT_WINDOW` cannot help: its
documented range is 100000 to 1000000, and the session uses the smaller of that and
the model window. The combination at 32768 is not confirmed by the documentation;
watch the first long session. If the gateway returns a context error, Claude Code
may not recognise it as "too long" and will not compact by itself: run `/compact`.

Smoke test (inside a scratch git repo with `hello.txt`):

1. Read: `Read hello.txt and tell me what it says.`
2. Edit: `Change the word "hello" to "goodbye" in hello.txt.` Check `git diff`.
3. Command: `Run "git status --short" and report the output.`

Also run `/status`: `Anthropic base URL` must show the gateway.

## Codex CLI

Docs: <https://learn.chatgpt.com/docs/config-file/config-advanced> and
<https://learn.chatgpt.com/docs/config-file/config-reference>
(formerly under developers.openai.com/codex).

`~/.codex/config.toml`:

```toml
model = "local-qwen"
model_provider = "gateway"
review_model = "local-qwen"
model_context_window = 32768
model_auto_compact_token_limit = 24000

[model_providers.gateway]
name = "Household gateway"
base_url = "https://llm.example.com/v1"
env_key = "HEARTH_LLM_KEY"
wire_api = "responses"

[agents]
default_subagent_model = "local-qwen"

[memories]
consolidation_model = "local-qwen"
extract_model = "local-qwen"
```

```bash
export HEARTH_LLM_KEY=sk-REPLACE
```

The reference documents `wire_api` as accepting `responses` only (also the default),
so Codex speaks `POST /v1/responses`. Whether the `base_url` should include `/v1`
is not stated in the pages read; the example above assumes it does, as the OpenAI
convention is. If the first request 404s, try without `/v1`.

Model-name settings the reference lists: `model`, `review_model`,
`agents.default_subagent_model`, `memories.consolidation_model`,
`memories.extract_model`. Set all to `local-qwen`: `review_model` defaults to the
session model, but the subagent and memory models are separate requests, and one
left at a default would be asked of the gateway by a cloud name. The reference
documents no guardian or compaction model key.

Context: `model_context_window` and `model_auto_compact_token_limit` are documented
keys. The 24000 compaction threshold is a choice that leaves room for the reply; it
is not from the documentation.

Smoke test: same three prompts as above, in a scratch repo. Codex asks before editing
and before running commands unless its approval mode says otherwise.

## oh-my-pi

Docs: <https://omp.sh/docs/providers>, <https://omp.sh/docs/custom-models>,
<https://omp.sh/docs/roles>.

`~/.omp/agent/models.yml`:

```yaml
providers:
  hearth:
    baseUrl: https://llm.example.com/v1
    api: openai-completions
    apiKey: HEARTH_LLM_KEY        # env var name first, otherwise literal text
    models:
      - id: local-qwen
        name: Local Qwen
        supportsTools: true
        contextWindow: 32768
        maxTokens: 4096
```

`~/.omp/agent/config.yml` (roles; every role set so none falls to another model):

```yaml
modelRoles:
  default: hearth/local-qwen
  smol: hearth/local-qwen
  slow: hearth/local-qwen
  plan: hearth/local-qwen
  commit: hearth/local-qwen
  vision: hearth/local-qwen
  designer: hearth/local-qwen
  tiny: hearth/local-qwen
  task: hearth/local-qwen
  advisor: hearth/local-qwen
```

```bash
export HEARTH_LLM_KEY=sk-REPLACE
omp models hearth        # validates the file and lists the model
```

Why every role: roles for titles, commits, subagents and the advisor are separate
requests. The roles page says an unassigned `advisor` looks for a separate strong
model and does not inherit `default`, and unassigned roles pick any authenticated
model, which on a laptop with other providers signed in would not be `local-qwen`.
Also leave `retry.fallbackChains` unset: a fallback chain would send a failed turn
to another provider from the client side.

Context: `contextWindow` is documented as driving the context meter and overflow
handling, and defaults to 128000 if omitted, so it must be set. `compactionModel`
and `contextPromotionTarget` are per-model keys; leave them unset (promotion would
switch to a larger model). The roles page does not document a model key for
compaction or memory beyond these, and `omp config list` is the authoritative list
for the installed version.

Smoke test: `omp -p --model hearth/local-qwen "Reply with only OK"` first, then the
three prompts above in a scratch repo.

## Keys: one per tool, `local-qwen` only

Mint one virtual key per tool so each can be revoked and its spend read separately.
With the master key (held by the owner, never in a tool's config):

```bash
for tool in claude-code codex omp; do
  curl -sS https://llm.example.com/key/generate \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"key_alias\": \"$tool\", \"models\": [\"local-qwen\"]}"
done
```

The `models` list is the allowlist (LiteLLM virtual keys documentation:
<https://docs.litellm.ai/docs/proxy/virtual_keys>). Store each returned key where
the tool reads it (`ANTHROPIC_AUTH_TOKEN`, `HEARTH_LLM_KEY`), not in a repository.

Confirm the gateway refuses the key for any other model. Use a cloud name from the
catalogue:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://llm.example.com/v1/chat/completions \
  -H "Authorization: Bearer sk-REPLACE" -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"hi"}]}'
```

Expect a 4xx (LiteLLM source, `auth_checks.py`: "key not allowed to access model.
This key can only access models=['local-qwen']"), and the same for
`claude-sonnet-5`. Then the control, which must return 200:
the same request with `"model":"local-qwen"`. If the cloud request succeeds, the key
was minted without `models`; delete it and mint again.

## Known limits

* **Claude Code is the least practical of the three.** Its fixed prompt (system
  prompt plus tool definitions) takes most of a 32k window, leaving little for the
  conversation, and it compacts only by its own rules. Use it for short tasks, or
  prefer Codex or oh-my-pi.
* **Oversized prompts are refused, not truncated.** The chart sets
  `router_settings.enable_pre_call_checks: true`; LiteLLM raises
  `ContextWindowExceededError` when a prompt exceeds `max_input_tokens` (32768).
  It refuses cleanly only while no fallback exists, which the chart tests enforce.
  It is read at startup, so a change needs a LiteLLM restart, and a value saved in
  the admin UI's router settings can override the file.
* **The check counts the prompt, not the reply.** A prompt just under 32768 leaves
  no room to answer; compaction thresholds in the tools should sit well below it.
* **Cold turns are slow** (about 630 tokens per second) and the cache is lost at
  unload.
* **One request at a time.** A tool run in parallel, or a subagent, queues behind
  the active turn; short client timeouts will fire.
* **Memory pressure.** A full browser plus a loaded model swaps the laptop.
* **Bridged APIs.** Messages and Responses are translated to Chat Completions for
  the model; a tool-call format the translation drops shows up as a tool that "does
  nothing". Compare against oh-my-pi, which needs no translation, before blaming the
  model.
* **Smaller model.** Expect more failed edits and shorter plans than with a cloud
  model; the local lane is for work the owner is content to keep on the laptop.
