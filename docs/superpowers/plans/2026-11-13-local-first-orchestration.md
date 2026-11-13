# Local-first plan: orchestration brief

**Status:** proposed, for review.
**Date:** 2026-11-13.
**Design and reasons:** [`2026-11-13-local-first-increments.md`](2026-11-13-local-first-increments.md) ("the plan").
**home-ops wiring and cutover order:** `home-ops/docs/plans/2026-11-13-hearthai-local-inference.md` ("the companion").

This file turns the plan into work packages for one orchestrator (`claude-opus-5-5`) directing
executors (`claude-sonnet-5-5`). The plan says what and why. This file says who does which piece,
in what order, what they are told, and how the result is checked. Where the two disagree, the plan
wins and this file is corrected.

## For the orchestrator

You are running a multi-week change across two repositories for one owner, Josh. You do the
thinking that needs the whole picture: sequencing, writing specs, reviewing diffs, deciding whether
a package is done. Executors do one bounded package each and start with no context except the brief
you give them.

**What makes this job hard.** Three things, in order of how much damage they can do:

1. **A merge to `hearthai` `main` deploys.** home-ops follows that branch. So you never merge, and
   every chart package must be a no-op for the running cluster until home-ops turns it on. A
   package that adds a required value, renames a resource, or changes a default that home-ops
   already renders is wrong even if its tests pass.
2. **Nobody in this run can see the cluster or the laptop.** No kubeconfig, no Ollama. Passing
   tests show that manifests render as intended. They do not show that anything works. Report the
   two separately and never write "verified" for something only the owner can observe.
3. **The private lane must fail closed.** A request for a local model name goes to the laptop or
   fails. Any change that could give it a second backend, a fallback, an alias or a router is a
   defect, whatever else it achieves. Read "What private promises" in the plan before reviewing
   anything that touches the LiteLLM catalogue.

**Your loop for each package:**

1. Check its dependencies and gates in the table below. If a gate is open, stop and ask the owner
   the gate's question. Do not start packages behind an open gate, and do not treat silence as a yes.
2. Send the executor the brief. Add anything a previous package's report changed (a renamed
   setting, a value that turned out different). Briefs are written to stand alone; keep them so.
3. When the executor reports, read the diff yourself and rerun the acceptance commands. An
   executor's summary is evidence, not a result. Check specifically for: work outside the package's
   file list, a test weakened to pass, a real address or secret in any file, and a claim with no
   command output behind it.
4. If it falls short, send it back with the specific failure. If the brief was wrong, fix the brief
   here first.
5. Record the outcome in the status column and tell the owner what is ready for them, in one or two
   sentences, with the pull request link.

**Git.** One branch per package, named in the table, branched from the default branch of its
repository unless the table says it continues another. Commit when the acceptance commands pass.
Once your own review passes, push the branch and open a pull request, always. Executors do not
push; you do. In the pull request body say which package it is, which gates must be open before
it merges, what the tests showed, and what only the owner can check once it is live. Follow the
repository's pull request template if it has one. Never merge, never enable auto-merge, and never
push to `main`, in either repository. A pull request behind a closed gate is opened as a draft
with the gate named in the first line.

**Workspace.** The session has both repositories checked out. Give each executor the path of the
one repository its package names, and tell it not to touch the other.

**Tooling.** `uv` is expected; `helm` may be missing. If an acceptance command cannot run because a
tool is absent and cannot be installed, the package is not done: report which command did not run.
CI in each repository is the backstop, not a substitute.

**Packages that need a spec.** S1 to S3 are yours, not an executor's. Write the spec in
`docs/superpowers/specs/`, get the owner's approval, then break it into packages in the same form
as the ones below and append them to this file. Do not hand an unspecified design to an executor.

**Decisions.** The plan's open decisions (O1 to O10) have recommendations. Act on the
recommendation unless the owner has said otherwise, and name the decision in your report when a
package depends on one. Decisions that are settled:

- O3: the advertised local context is **32768**. Measured on the laptop: a 42k-token prompt pushed
  it into heavy swap beside a normal desktop. Do not raise it.
- `OLLAMA_KEEP_ALIVE` stays at Ollama's default in all documentation.

## For every executor (include this section in every brief)

You are doing one package of a larger plan for a household AI deployment. Another agent reviews
your work and reruns your checks, so say plainly what you did and did not do.

- **Stay inside the package.** Change only the files listed. If the task needs another file, stop
  and report why instead of editing it. Do not tidy, rename or refactor nearby code.
- **No cluster, no laptop.** You cannot run `kubectl`, `flux` or reach any model. Do not write
  that something "works"; write what rendered and which tests passed.
- **Secrets and addresses never go in a file.** No real IP, hostname, domain, key or token. Use the
  placeholders the brief gives. In home-ops, addresses are `${SECRET_*}` substitution variables and
  secrets are `ExternalSecret` references. Do not try to decrypt `*.sops.yaml`.
- **Confirm upstream names before using them.** Setting names for Open WebUI, LiteLLM and Caddy in
  the briefs come from documentation, not from the pinned versions. Check each against the source
  at the pinned tag. A misspelt setting is silently ignored, which here means a protection that
  looks present and is not. If a name is different or gone, use the right one and report the change.
- **Do not weaken a test to make it pass.** If an existing test conflicts with the task, report it.
- **Commit on the branch you were given. Do not push, open a pull request or merge.** The
  orchestrator does that after reviewing your work.

Report back in this shape, as plain text:

1. What changed, file by file, one line each.
2. Each acceptance command and its actual result. Mark any that did not run and why.
3. Every upstream name you confirmed, with where you confirmed it, and any that differed.
4. Anything you were unsure of or chose between, and what you chose.
5. Anything the owner must do or check that you could not.

## Gates (owner only)

| Gate | Question for the owner | Opens |
|---|---|---|
| G1 | Has the I1 branch merged, and is the `hearthai` release Ready with no new objects and an unchanged model list? | H1 |
| G2 | Is `SECRET_LOCAL_LLM_HOST` populated in `cluster-secrets`, and is the laptop set up per the plan (Ollama exposed, stable address, context 32768)? | H1 merge |
| G3 | After H1: does the persona answer with the laptop awake **and the status tile show up**, and does it fail in about 3 s with the fixed message, tile down, when the laptop is asleep? | everything after M1 |
| G4 | Does the LiteLLM admin UI show any model that exists only in the database? Which? | P4 |
| G5 | Is the spec approved? (asked once per spec) | packages derived from it |

## Packages

Status values: `todo`, `running`, `review`, `ready for owner`, `merged`, `blocked: <why>`.

| ID | Package | Repo | Who | Branch | Depends on | Status |
|---|---|---|---|---|---|---|
| V1 | Confirm Open WebUI setting names at the pinned tag | hearthai (read-only) | executor | none | — | todo |
| V2 | Confirm LiteLLM behaviours at the pinned tag | hearthai (read-only) | executor | none | — | todo |
| V3 | Confirm Meridian does not run built-in tools | hearthai (read-only) | executor | none | — | todo |
| P1a | Local lane entries and privacy tests | hearthai | executor | `plan/i1-local-lane` | V2 | todo |
| P1b | Gate, its network policy, docs | hearthai | executor | continues `plan/i1-local-lane` | P1a | todo |
| P1c | Gate behaviour test | hearthai | executor | continues `plan/i1-local-lane` | P1b | todo |
| P1d | Runtime lane test and surface lane test | hearthai | executor | continues `plan/i1-local-lane` | P1b, V2 | todo |
| H1 | Enable the lane and add the status probe | home-ops | executor | `plan/h1-local-lane` | G1 | todo |
| H9 | Pin the hearthai source and automate the bump | home-ops | executor | `plan/h9-pin-hearthai` | G3 | todo |
| P3 | Open WebUI deny table and drift check | hearthai | executor | `plan/i3-deny-table` | V1, V3, G3 | todo |
| P4 | Catalogue in the chart only | hearthai | executor | `plan/i4-catalogue` | G3, G4 | todo |
| P7 | Coding-agent runbook and context check | hearthai | executor | `plan/i7-coding-agents` | G3, V2 | todo |
| H7 | Ingress timeouts for the gateway | home-ops | executor | `plan/h7-llm-ingress` | G3 | todo |
| S1 | Spec: automatic router (plan M3) | hearthai | orchestrator | `plan/spec-router` | G3 | todo |
| S2 | Spec: personas at the gateway (plan I11) | hearthai | orchestrator | `plan/spec-personas` | S1 merged | todo |
| S3 | Spec: executor, memory tools, lane gate (plan M6) | hearthai | orchestrator | `plan/spec-ai-jobs` | G3 | todo |

V1 to V3 can run in parallel. P1a to P1d are sequential on one branch and merge as one pull
request; do not open it until P1c and P1d pass, because they are the only evidence for what the
gate and the lane do at runtime. P1c and P1d can run in parallel with each other. After G3, P3, P4, P7, H7 and the specs are independent of each other.

The plan's I2 persona, I9 and I10 are owner steps with no package. Remind the owner of them when
their milestone is reached.

---

### V1. Confirm Open WebUI setting names

**Why:** package P3 turns off every tool path in Open WebUI through environment settings. The names
in the plan's I3 table were written from documentation for older versions. A wrong name does
nothing and leaves the feature on.

**Do:** read the Open WebUI image tag from `deploy/charts/hearthai/values.yaml`
(`openwebui.image.tag`). In the upstream repository at exactly that tag, read
`backend/open_webui/config.py` and `backend/open_webui/env.py`. For every setting named in the
plan's I3 table, record whether it exists at that tag, its default, and whether an environment
value overrides it when `ENABLE_PERSISTENT_CONFIG` is false. Then search the same files for
settings the table does not list that enable model-callable tools, code execution, outbound
fetching, external tool or terminal servers, scheduled runs, or a second model path, and list them.

**Change no files.**

**Done when:** you return a table with one row per setting: name in the plan, actual name at the
tag, default, and the value that disables it; plus the list of additional settings found, each with
a one-line description of what it enables. Say which rows you read from source and which you
inferred.

### V2. Confirm LiteLLM behaviours

**Why:** P1a, P4 and P7 depend on four LiteLLM behaviours stated from documentation.

**Do:** the running version is not the chart's pin. Read both: `litellm.image.tag` in
`deploy/charts/hearthai/values.yaml`, and the override in
`home-ops/kubernetes/apps/ai/hearthai/app/helmrelease.yaml`. Use the home-ops version. From
LiteLLM's source and documentation at that version, answer:

1. Does `model_info.disable_background_health_check: true` exclude one model from the background
   health loop?
2. With `store_model_in_db: false`, do virtual keys, budgets and spend still work from Postgres?
   Which UI-edited settings stop loading?
3. What is the exact option that makes the proxy refuse a request larger than a model's
   `max_input_tokens` before sending it, and where is it set?
4. Which configuration keys can cause a request for one model name to be served by a different
   deployment (fallbacks of every kind, aliases, wildcards, default models)? List every one.
5. Does the proxy serve `/v1/messages` and `/v1/responses` for an `ollama_chat/` model, including
   tools and streaming? Is there a token-counting route for non-Anthropic models?

**Change no files.**

**Done when:** each question has an answer, the file or page it came from, and a confidence of
"read in source", "read in docs" or "could not confirm". Question 4's list becomes the test in P1a,
so completeness matters more there than anywhere else.

### V3. Confirm Meridian's tool behaviour

**Why:** Meridian serves a Claude subscription by wrapping Claude Code. If it lets Claude Code run
its built-in shell or web tools inside the Meridian pod, that is tool use in the cluster that
nothing else in the plan controls.

**Do:** read the Meridian version from the home-ops HelmRelease override (not the chart's pin).
In its source at that version, find how a proxied request is turned into a Claude Code call, and
whether built-in tools and MCP servers are disabled, passed through to the client, or executable in
the pod. Find any setting that controls it.

**Change no files.**

**Done when:** you state which of the three it is, quote the code or setting that decides it with
file and line, and name the setting to pin if one is needed.

### P1a. Local lane entries and privacy tests

**Why:** this is the first half of the plan's I1. It lets the chart declare a model name that is
served only by a laptop on the owner's network. Private personas will be bound to it, and privacy
is the whole reason they exist, so the tests in this package matter more than the feature: they are
what stops a later change from quietly giving that name a cloud backend.

**Context you need:** `deploy/charts/hearthai/templates/litellm-configmap.yaml` renders the bundled
`files/litellm-config.yaml`. That file is hash-checked by `test_exact_home_ops_catalogue` and must
stay byte-identical. home-ops deploys this chart from `main` with its own values, so with default
values the rendered output must not change at all.

**Do:**

- Add to `values.yaml`: `localInference.enabled` (false), `localInference.upstreamUrl` (empty),
  and `localInference.models`, a list of `name`, `ollamaModel`, `contextWindow`, defaulting to one
  entry: `local-qwen`, `qwen3.6:35b-a3b`, `32768`. Describe them in `values.schema.json`.
- When enabled, the rendered LiteLLM config gains one `model_list` entry per model:
  `ollama_chat/<ollamaModel>`, `api_base` `http://<fullname>-local:11434` (the gate Service that
  P1b adds), `model_info` with `mode: chat`, `max_input_tokens` from `contextWindow`,
  `supports_function_calling: true`, and the health-check exclusion confirmed in V2. Insert them
  at render time. Do not edit the bundled file.
- Rendering with `enabled: true` and an empty `upstreamUrl` must fail with a clear message.

**Tests to add in `deploy/tests/test_hearthai_chart.py`:**

- default values: rendered output identical to before this change;
- enabled without `upstreamUrl`: render fails;
- every local name appears exactly once in `model_list` and its `api_base` is the gate Service;
- the rendered config contains none of the rerouting keys from V2 question 4, and no model name
  containing `*`. Write this as a list in the test so adding a key later is one line;
- no `litellm_params` anywhere in the catalogue references a provider API key other than
  `MERIDIAN_API_KEY`;
- the Open WebUI ConfigMap has no non-empty `TASK_MODEL` or `TASK_MODEL_EXTERNAL`.

**Files:** `deploy/charts/hearthai/values.yaml`, `values.schema.json`,
`templates/litellm-configmap.yaml`, `deploy/tests/test_hearthai_chart.py`.

**Acceptance:**

```sh
helm dependency build deploy/charts/hearthai
helm lint deploy/charts/hearthai -f deploy/examples/hearthai-values.yaml
uv run --no-project --with 'pytest>=8,<10' --with pyyaml python -m pytest deploy/tests -q
```

All existing tests still pass unmodified.

### P1b. Gate, network policy and docs

**Why:** second half of I1. LiteLLM uses one timeout for connecting and for reading, so on its own
it cannot tell a laptop that is gone (should fail in seconds) from a model working through a long
prompt (legitimately over a minute). The gate is a small reverse proxy that makes that distinction
and returns one fixed error. It is also the only place the laptop's address exists.

**Do:**

- New `templates/local-gate.yaml`, rendered only when `localInference.enabled`: a single-replica
  Deployment, a Service on 11434 and a ConfigMap, named `<fullname>-local`, with labels and helpers
  in the style of `templates/meridian.yaml`. Use the stock Caddy image, pinned in `values.yaml` as
  `localInference.gate.image`. No custom code.
- The proxy must: forward to the upstream URL, which it reads from an environment variable set
  from `localInference.upstreamUrl`; give up dialling after 3 seconds; actively probe
  `GET /api/version` every 10 seconds with a 2 second timeout; stream responses without buffering;
  place no limit on response time once connected; and answer every upstream failure with status
  503 and this JSON body, exactly:
  `{"error":{"type":"local_unavailable","message":"Local model unavailable: the laptop is off, asleep or off the network. This model never falls back to another provider."}}`
  Confirm each directive against Caddy's documentation for the pinned version. If you can run the
  image, validate the config with it and include the output.
- The upstream URL must appear only in the Deployment's environment. Not in the ConfigMap.
- Pod settings to match the other workloads: no service-account token, non-root, all capabilities
  dropped, read-only root filesystem if the image allows it (say if it does not).
- A `CiliumNetworkPolicy` like Meridian's: ingress from this release's LiteLLM pods and node
  probes, plus whatever is listed in `localInference.gate.networkPolicy.additionalIngress`. That
  value is a list of entries with a `namespace` and `matchLabels`, default empty; each becomes one
  `fromEndpoints` selector scoped to that namespace. The owner's status-page prober runs in another
  namespace and must be admitted this way, or its tile reads down while the laptop is serving.
  Reuse the `meridian.networkPolicy` pattern, including a switch to disable the policy.
- A response-header timeout, `localInference.gate.responseHeaderTimeout`, default `180s`, so an
  upstream that accepts a connection and never answers is bounded. Do not set it lower by default:
  a cold 32k prompt was measured at about 50 seconds before the first byte.
- Add the gate image to the Renovate custom manager's coverage if its existing pattern does not
  already match.
- `deploy/README.md`: a "Local lane" section. Take the content from the plan's "What private
  promises" and "Practical limits of the laptop" sections, shortened. `templates/NOTES.txt` and
  `deploy/examples/hearthai-values.yaml`: mention the option, left disabled, with the placeholder
  `http://laptop.example.internal:11434`.

**Tests to add:** disabled renders no gate objects; enabled renders exactly one Deployment, Service,
ConfigMap and policy for the gate; the upstream URL string is absent from every ConfigMap; the gate
pod mounts no Secret and has `automountServiceAccountToken: false`; with `additionalIngress` empty
the policy's only endpoint ingress selector is this release's LiteLLM labels; with one entry
supplied the policy has exactly that one more selector, scoped to the entry's namespace. Update `test_cohesive_release_and_oidc` only if
its Deployment count now depends on the option, and keep its default-values assertion.

**Files:** new `templates/local-gate.yaml`; `values.yaml`, `values.schema.json`, `templates/NOTES.txt`,
`deploy/tests/test_hearthai_chart.py`, `deploy/README.md`, `deploy/examples/hearthai-values.yaml`,
`.github/renovate.json5`.

**Acceptance:** the three commands from P1a, plus the default render compared before and after:

```sh
helm template hearthai deploy/charts/hearthai -f deploy/examples/hearthai-values.yaml > /tmp/after.yaml
```

against the same command on `main`. The only differences allowed are none.

### P1c. Gate behaviour test

**Why:** P1b's tests show the gate's configuration renders. They cannot show what the proxy does
when the laptop disappears, and that behaviour is the user-visible half of the feature. A dial
timeout does not bound an upstream that accepts and then hangs, and an active probe lags an outage.
This package proves the behaviour with the real image, or finds out that stock Caddy cannot do it.

**Do:** under `deploy/tests/gate/`, a test that renders the chart with the lane enabled, extracts
the gate's configuration, runs the pinned Caddy image with it, and drives it against scripted fake
upstreams. Use only Python's standard library for the fakes. Assert each row, with wall-clock
bounds:

| Upstream | Required result |
|---|---|
| Connection refused | `503`, the exact JSON body from P1b, `Content-Type: application/json`, under 1 s |
| SYN blackholed (packets dropped, no reset) | same response, within the dial timeout plus 1 s |
| Accepts, never answers | same response, within the response-header timeout (render it short for the test) |
| Marked down by the active probe | same response immediately, without a dial |
| Sends headers and chunks, then drops | the client sees the stream fail promptly and detectably. A `503` cannot be sent after bytes have gone out; assert the truncation, do not pretend otherwise |
| Silent for longer than the probe interval and the dial timeout, then streams for at least two minutes | delivered complete, unbuffered, chunk timing preserved |
| Healthy, gone, healthy again | new requests fail within one probe interval and recover within one |

Make the blackhole case deterministic. A route that happens to be unreachable on one CI runner is
not good enough; drop the packets explicitly in a network namespace or container network you
control, and say how you did it. Add the test as a job in `.github/workflows/ci.yaml`. Note in
`deploy/README.md` the two cases where the fixed message is not what the user sees.

**If a row cannot be met with stock Caddy and configuration alone, stop.** Report which row, what
Caddy does instead, and what you tried. Do not add a plugin, a custom build or a wrapper script.
The fallback is a purpose-built proxy and that is the owner's decision.

**Files:** new `deploy/tests/gate/`; `.github/workflows/ci.yaml`; `deploy/README.md`; and
`templates/local-gate.yaml` only to fix a behaviour the test exposes.

**Acceptance:** the new test passes locally with the pinned image, and you include its full output.
If you cannot run containers, the package is not done; say so.

### P1d. Runtime lane test and surface lane test

**Why:** P1a asserts that the rendered config has no fallback, alias or wildcard keys. That is
worth keeping but it is not proof of where a request goes, and it will mean less once a routing
hook and personas are added. The owner wants the property itself tested: a request for a local
model, with the laptop down, reaches no cloud provider. Later packages (the router and personas)
must extend these tests, so build them to be extended.

**Do, part one, the gateway:** under `deploy/tests/lane/`, a test that starts the LiteLLM image at
the version home-ops runs with the chart-rendered config, on a container network with no outside
egress. The local upstream is down. Every cloud route is pointed at, or can only reach, a
recording stand-in. Use V2's answers for how to redirect each provider; if a provider's address
cannot be redirected, the closed network plus the attempt log below is the evidence, and say so.
For each local model name, send a request through `/v1/chat/completions`, `/v1/responses` and
`/v1/messages`, streaming and not. Assert: the response is the gate's `503`; the recorders
received zero requests; and LiteLLM attempted no deployment other than the local one. Take the
last from a logging callback that records every attempted deployment, not from the absence of
errors. Structure it so a later package can load a hook module and a persona table and rerun the
same assertions for more model names.

**Do, part two, the surface:** under `deploy/tests/surface/`, a test that runs the pinned Open
WebUI image with the chart-rendered environment in local-auth mode against a recording fake
gateway that lists one local and one cloud model. Through Open WebUI's API, hold a short chat on
the local model and wait for background tasks to finish. Assert every request the fake gateway
received for that chat, including title, tag and follow-up generation, named the local model.

**CI:** part one runs on every chart change. Part two is heavy; run it on changes to the Open WebUI
tag or its ConfigMap template and on a weekly schedule.

**Files:** new `deploy/tests/lane/`, new `deploy/tests/surface/`, `.github/workflows/ci.yaml`,
`deploy/README.md` (one paragraph on what each test shows and does not).

**Acceptance:** both tests pass locally and you include their output. Then show each can fail: add
a fallback from a local name to a cloud name in a scratch copy of the config and show part one
fails; set a task model in a scratch environment and show part two fails. Revert both.

### H1. Enable the lane in home-ops

**Why:** the chart can now route to a laptop but is switched off. This turns it on for the owner's
cluster and puts the lane on the status page. Read `CLAUDE.md` in the home-ops repository first; its
rules on addresses, secrets and cutover order apply to every line you write.

**Do:**

- `kubernetes/apps/ai/hearthai/app/helmrelease.yaml`: under `values`, add
  `localInference.enabled: true` and
  `localInference.upstreamUrl: "http://${SECRET_LOCAL_LLM_HOST:=local-llm.invalid}:11434"`.
  Add a comment in the file's existing style saying: the address is the only site input; the
  `.invalid` default makes the lane fail closed until the owner sets the variable; the lane must
  never be given a fallback.
- In the same block, admit the status-page prober through the gate's policy:
  `localInference.gate.networkPolicy.additionalIngress` with one entry for the Gatus pods. Gatus
  runs in the `observability` namespace; read its pod labels from
  `kubernetes/apps/observability/gatus/app/helmrelease.yaml` and the chart it uses, and use the
  narrowest label set that selects only those pods. Without this the probe below is denied and
  the tile stays down.
- `kubernetes/apps/ai/hearthai/app/gatus.yaml`: one more endpoint, `local-inference`, group
  `internal`, probing `http://hearthai-local.ai.svc.cluster.local:11434/api/version` every minute
  for status 200. No alerting: the laptop being asleep is normal. Say so in a comment.
- `kubernetes/apps/ai/hearthai/README.md`: a short "Local lane" section, and the owner steps from
  the companion.

Do not add or edit anything in `kubernetes/flux/vars`. The owner populates the variable.

**Files:** the three above only.

**Acceptance:**

```sh
task k8s:flate
yamllint kubernetes/apps/ai/hearthai
pre-commit run --all-files
```

Also confirm by search that the diff contains no IP address and no hostname other than
`local-llm.invalid` and the in-cluster Service name.

**Report additionally:** the Service name assumes release name `hearthai`; confirm it from the
chart's helper. Give the Gatus labels you selected and where you read them. State that this must
not merge before gates G1 and G2, and that the owner's check after merge includes the tile being
**up** with the laptop awake, which is the only evidence the policy admits the prober.

### H9. Pin the hearthai source and automate the bump

**Why:** home-ops deploys whatever lands on hearthai `main`. Once the chart carries security
settings, that should be a reviewed step. But a pin with no update path is worse than floating:
the deployment silently stays on an old chart while fixes land upstream. So the pin and its update
mechanism ship together or not at all.

**Do:**

- `kubernetes/flux/repositories/git/hearthai.yaml`: replace `ref.branch: main` with `ref.commit`
  set to the current head of hearthai `main`. Rewrite the header comment, which describes the
  source as floating.
- `.github/renovate.json5`: make Renovate propose a pull request when hearthai `main` moves.
  First check whether the existing `flux` manager already updates a `GitRepository` commit pin; the
  `mindroom` source in the same directory is pinned the same way, so look at whether it has ever
  received a Renovate pull request. If the manager does not cover it, add an annotated pin using
  the repository's existing `# renovate:` comment convention and the `git-refs` datasource tracking
  `main`. Group and schedule it weekly so each hearthai commit is not its own pull request, and do
  not automerge it.
- `kubernetes/apps/ai/hearthai/README.md` ("Upgrading") and the comments in
  `kubernetes/apps/ai/hearthai/app/helmrelease.yaml` that say the chart floats or that a push to
  hearthai deploys: rewrite to describe the pinned flow, including the review step (read the
  hearthai diff under `deploy/`, `flate` passes, merge) and how to bump by hand the same day for an
  urgent fix.

**Files:** those four.

**Acceptance:** `task k8s:flate`, `yamllint kubernetes/flux/repositories kubernetes/apps/ai/hearthai`,
and Renovate's config validator on `.github/renovate.json5`. Search the repository for "float" and
"main" near "hearthai" and confirm no stale description remains.

**Report additionally:** whether Renovate will actually pick the pin up is only shown by its next
run. Say that plainly and tell the owner what to look for.

### P3. Open WebUI deny table and drift check

**Why:** plan priority 5 and increment I3. Open WebUI must be a chat surface with no tool use. Tools
will come later from sandboxed pods; until then anything that lets a model or a user run code or
reach the network from the Open WebUI pod is an unplanned execution path next to that pod's
secrets. Settings alone are fragile, because Renovate bumps Open WebUI and upstream renames
settings, so this package also adds the check that notices.

**Inputs:** the V1 and V3 reports, pasted into this brief by the orchestrator. Use V1's confirmed
names, not the plan's.

**Do:**

- `templates/configmap.yaml`: add every disabling setting from V1 that is not already present. Keep
  the existing comment style; one comment per group saying what it closes.
- If V3 found a Meridian setting to pin, add it in `templates/meridian.yaml`.
- `deploy/tests/test_hearthai_chart.py`: one test holding the full table as a literal mapping of
  setting to required value. It fails if a key is missing or different.
- New `deploy/tests/check_openwebui_env.py`: reads the pinned tag from `values.yaml`, fetches that
  tag's `config.py` and `env.py` from the upstream repository, and exits non-zero naming any
  setting in the table that does not appear. It must import the table from one place shared with
  the test, not copy it. Run it as a step in the chart job in `.github/workflows/ci.yaml`.
- `deploy/README.md`: replace the sentence about code execution being disabled with the full list,
  and state the residual risk in plain words: an administrator can still install an in-process
  function, no setting prevents that, and the control is that none is installed.

**Files:** as named above.

**Acceptance:** the three chart commands, plus
`uv run --no-project python deploy/tests/check_openwebui_env.py` exiting zero. Then change one
setting name in the table to a nonsense name, show the check fails, and revert.

### P4. Catalogue in the chart only

**Why:** plan I4. With `store_model_in_db` on, a model added in the LiteLLM admin UI is stored in
Postgres and merged with the chart's list. A cloud deployment added there under a local name would
be load-balanced with the laptop. Turning it off makes the chart file the only catalogue.

**Inputs:** V2's answer to question 2, and the owner's answer to gate G4. If G4 named a
database-only model that is in use, the orchestrator adds its definition to this brief and you add
it to the catalogue in the same change.

**Do:** set `store_model_in_db: false` in `files/litellm-config.yaml` and remove or flip the
matching `STORE_MODEL_IN_DB` value in `templates/litellm.yaml`. Correct the comment above the
setting: keys, budgets and spend live in Postgres regardless. Update the hash in
`test_exact_home_ops_catalogue`, the environment assertion in
`test_proxy_runtime_and_internal_wiring`, and the passages in `deploy/README.md` that call the file
a byte-for-byte import of home-ops; from this change it is HearthAI's own catalogue.

**Files:** `files/litellm-config.yaml`, `templates/litellm.yaml`, `deploy/tests/test_hearthai_chart.py`,
`deploy/README.md`.

**Acceptance:** the three chart commands.

**Report additionally:** this changes the running proxy when merged. List what the owner should
check afterwards (each consumer's key still works; the UI's model list equals the chart's).

### P7. Coding-agent runbook and context check

**Why:** plan M4. The owner wants Claude Code, Codex CLI and oh-my-pi on the laptop to use the local
model through the gateway, so one endpoint and one kind of key serve everything.

**Facts measured on the owner's laptop, to state in the runbook:** context is 32768; a cold prompt
evaluates at roughly 630 tokens per second, so a 20k-token first turn waits about 30 seconds; an
identical prefix is reused on the next request (0.2 s instead of 66 s for 42k tokens); the cache is
lost when the model unloads after Ollama's keep-alive; one request is served at a time; with a
full browser open the laptop swaps whenever the model is loaded.

**Do:**

- New `docs/runbooks/coding-agents.md`, in the style of `docs/runbooks/webfetch.md`. One section
  per tool. For each: the API it speaks and the gateway route; the exact configuration, taken from
  that tool's current documentation, with `https://llm.example.com` and `sk-REPLACE` as
  placeholders; every model-name setting the tool has, all set to `local-qwen`, with a sentence on
  why (a background model left at its default would be requested from the gateway by a cloud
  name); the context settings; and a three-step smoke test: read a file, edit it, run a command.
  Then a shared section on keys: one virtual key per tool, allowlisted to `local-qwen` only, and how
  to confirm the gateway refuses that key for any other model. Then known limits. Say that Claude
  Code's fixed prompt takes most of a 32k window and is the least practical of the three.
- Enable the pre-call context check from V2 question 3 so an oversized request is refused instead
  of truncated. Add a chart test for it.
- Link the runbook from `deploy/README.md`.

**Files:** new `docs/runbooks/coding-agents.md`; `deploy/README.md`; the chart file V2 identified
for the context check; `deploy/tests/test_hearthai_chart.py`.

**Acceptance:** the three chart commands. In your report, give the documentation URL each tool's
configuration came from, and mark any setting you could not confirm.

### H7. Ingress timeouts for the gateway

**Why:** coding CLIs reach LiteLLM through the `llm.` ingress. A cold local prompt can take close to
a minute before the first byte, which is the ingress controller's default read timeout, so
responses would be cut.

**Do:** in `kubernetes/apps/ai/hearthai/app/helmrelease.yaml`, add `litellm.ingress.annotations`
with the same four `nginx.ingress.kubernetes.io` timeout, body-size and buffering annotations the
Open WebUI `ingress.annotations` block in the same file already has, keeping the existing
`hajimari.io/icon` value the chart sets by default. Check how the chart merges annotations so the
icon is not lost. One comment explaining the reason.

**Files:** that file only.

**Acceptance:** `task k8s:flate`, `yamllint kubernetes/apps/ai/hearthai`.

---

## Specs the orchestrator writes

**S1, automatic router.** Brief: the plan's Milestone 3. The fail-closed table there is the
contract and becomes the unit tests. The spec must also require extending P1d's runtime lane test
with the hook loaded: every local name, and `auto` in every fail-closed row, with zero recorded
cloud calls. No router package merges without it. Settle in the spec: the rule set and where the term list comes
from; the classifier prompt and its exact accepted answer; how the hook reaches the local model
without going back through itself; what is logged; how shadow mode is switched. Then packages for
the module and its tests, the chart mount, the `auto` entry, the home-ops Secret, and the runbook.

**S2, personas at the gateway.** Brief: plan I11 and O5. Depends on S1's hook existing. Settle the
values shape, how the prompt is injected for each of the three request formats, and the test that a
persona cannot leave its declared lane, which is P1d's runtime lane test rerun with the persona
table loaded and the laptop down.

**S3, executor and first tools.** Brief: plan Milestone 6, the existing `service/ai_jobs` contracts
and `ARCHITECTURE.md` "Execution substrate". Settle the executor's Kubernetes interface, the
sandbox namespace inputs home-ops must provide first, the `memory-recall-v1` profile, and how a
store token reaches one run. Memory write and webfetch each get their own spec after recall is
live; the webfetch one starts with the spike in plan I15.
