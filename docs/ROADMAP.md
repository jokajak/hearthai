# HearthAI Roadmap

**Status:** authoritative capability roadmap<br>
**Last updated:** 2026-09-07<br>
**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)<br>
**Design detail:** [`superpowers/specs/2026-08-30-capability-roadmap-design.md`](superpowers/specs/2026-08-30-capability-roadmap-design.md)

## Direction

HearthAI begins by adopting OpenWebUI as a useful chat platform, then adds HearthAI's distinctive shared-memory skill and service, then takes ownership of web fetching so untrusted pages are read only by a quarantined model and reach the privileged context as scrubbed distillations, then governed MCP interoperability.

The near-term roadmap does **not** build a custom web UI, prescribe a HearthAI persona, replace OpenWebUI's personal memory, or introduce HearthAI user accounts.

```text
0.1 OpenWebUI foundation
  ↓
0.2 HearthAI shareable memory
  ↓
0.3 quarantined web fetch with hearthfetch
  ↓
0.4 governed MCP
  ↓
future verified users and richer sharing semantics
```

## Product boundaries

| Layer | Near-term owner | Responsibility |
|---|---|---|
| Browser chat | OpenWebUI | UI, streaming, conversations, personal memory, OIDC sessions |
| Inference | LiteLLM | Model/provider routing behind one endpoint |
| Shared-memory behavior | HearthAI Agent Skill | When to recall, share, ask approval, and report failures |
| Shared-memory state | HearthAI service | Stores, capability access, records, retrieval, audit |
| Web fetch | HearthAI `hearthfetch` tools | Fetch policy, deterministic input scrub, quarantined distillation, deterministic output scrub, opaque result handles, and brokering the search provider |
| General tools | Governed MCP boundary | Server/tool allowlists, credentials, audit, approvals |

OpenWebUI's native memory is the near-term personal-memory implementation. HearthAI's memory service initially owns only deliberately shareable stores. **Long-term personal-memory ownership is unresolved:** the roadmap commits neither to permanent OpenWebUI ownership nor to an eventual HearthAI migration.

## 0.1 — OpenWebUI foundation

### Purpose

Get a useful browser chat running with the fewest HearthAI-specific assumptions.

### Included

- OpenWebUI;
- generic OIDC with Authentik as the first provider;
- Josh-only admission;
- LiteLLM as the model endpoint;
- streaming responses;
- server-side conversation history;
- OpenWebUI built-in per-user memory and personalization controls;
- new, reopen, rename, and delete conversations;
- logout and session expiry;
- durable OpenWebUI secrets and database storage.

### Deliberately not included

- prescribed HearthAI persona or large system prompt;
- HearthAI shared-memory tool integration;
- external OpenAPI tools;
- web search and URL fetch;
- MCP;
- code execution or Open Terminal;
- public signup, invitations, or additional users;
- custom web frontend.

### Acceptance

1. Josh authenticates through Authentik and can log out.
2. An unauthenticated browser cannot access conversations or models.
3. Chat streams through the configured LiteLLM model alias.
4. Conversations survive browser and OpenWebUI restarts.
5. OpenWebUI personal memory can be added, recalled, reviewed, corrected, and deleted.
6. External tools, web access, MCP, and code execution are unavailable.

### What 0.1 teaches

- whether OpenWebUI is useful as a daily HearthAI platform;
- whether its native personal memory is valuable and trustworthy enough;
- whether Authentik, LiteLLM, and OpenWebUI are acceptable commodity boundaries;
- what HearthAI-specific behavior emerges organically from use.

## 0.2 — HearthAI shareable memory

### Purpose

Add HearthAI's distinctive capability: deliberate memory stores that can be accessed from multiple hosts or shared out of band without requiring a HearthAI multi-user account system.

### Included

- existing HearthAI shared-memory service;
- portable `shared-memory` Agent Skill;
- OpenWebUI tool binding to the same service;
- user-created named stores;
- capability-token access;
- out-of-band token sharing;
- store-specific recall;
- human approval before every shared write;
- author, timestamp, source, and audit history;
- the same store usable from OpenWebUI and at least one Agent Skills-compatible host;
- backup and human-readable export.

### Memory separation

```text
OpenWebUI native memory
  = near-term personal memory

HearthAI shared-memory service
  = deliberately shareable stores across hosts or people
```

0.2 does not migrate or replace OpenWebUI personal memory. It does not introduce a HearthAI private partition, account directory, invitation system, or verified membership model. This separation is a near-term release boundary, not a permanent decision about personal-memory ownership.

### Access model

Possession of a store capability token grants the access represented by that token. Tokens are transferred outside HearthAI's account system. This is simple and portable, but the release must document its security limitations explicitly.

The current service's unrevocable full-access tokens are implementation evidence, not necessarily the final 0.2 token shape. Before 0.2 is declared complete, token scope and revocation must either be implemented or accepted as explicit release limitations.

### Acceptance

1. Josh creates a named shared store.
2. OpenWebUI and one Agent Skills-compatible host access the same store.
3. A memory written from one host is recalled from the other.
4. No shared write occurs without approval of the exact content and destination store.
5. A host without the capability cannot list, read, or write the store.
6. Tokens never enter URLs, prompts, memory records, or logs.
7. Store data can be backed up and read independently of either host.

### What 0.2 teaches

- whether shareable memory provides value beyond OpenWebUI's personal memory;
- whether the skill is portable and understandable;
- whether out-of-band capability sharing is sufficient;
- whether revocation, read/write scopes, or verified membership are required next;
- whether personal memory should remain gateway-owned or move toward a HearthAI-owned boundary.

## 0.3 — Quarantined web fetch with `hearthfetch`

### Purpose

Ensure no untrusted web content ever enters OpenWebUI's model context. HearthAI fetches the
page, reads it in a sandbox that holds nothing worth stealing, and returns a
deterministically-scrubbed distillation directed at the question that was asked.

### The pattern

The dual LLM pattern. OpenWebUI's model is the **privileged LLM** — conversation, memories,
tools. `hearthfetch` runs the **quarantined LLM** — a page and a question, no tools, no
memory, no conversation, no credentials beyond its own. Three stages:

1. **Deterministic input scrub** — strip carriers a human reader would never see.
2. **Quarantined distillation** — the security boundary. Whatever the page says, it says it
   to something that cannot act and has nothing to leak.
3. **Deterministic output scrub** — what makes stage 2 hold.
4. **Quarantined classification** — a separate model reads only the scrubbed distillation and
   answers one question: does this contain instructions directed at the assistant reading it?
   A positive verdict rejects the source.

Stages 1 and 3 are code and load-bearing. Stage 4 is probabilistic and strictly additive: it
may reject, never permit, and no deterministic rule is relaxed because it exists. It is the
only layer that can touch plainly visible prose, which no deterministic test can distinguish
from legitimate content.

### Delivered as tools

`search_web`, `fetch_result`, and optionally `fetch_url`, published as an OpenAPI tool
server. HearthAI owns this contract. There is no combined `research()` orchestrator — that
is the withdrawn product returning by the back door.

A tool means the model supplies the question, so distillation is question-directed. It also
means the model composes the outbound request, which is an exfiltration primitive the
retrieval-pipeline alternative did not have: a page can describe a destination in prose that
survives URL scrubbing, and the model can act on it.

Hence **search results are opaque HMAC-signed handles, not URLs** — the privileged model
manipulates references, not values, so for every search-derived page it never sees, holds,
or composes a URL. `fetch_url` accepting a literal URL is the residual, and shipping it is
an open decision.

### Two decisions that carry the design

- **The model authors prose; the service authors every URL.** Nothing URL-shaped survives
  stage 3; anything that does drops the document rather than being repaired.
- **Detection rejects the source rather than cleaning it.** Stripping gives an attacker many
  attempts per page; rejection makes every payload have to evade detection at once. The
  design assumes the sanitiser is public — it is AGPL-3.0 — so nothing rests on the attacker
  not knowing the rules.
- **Never a fallback to raw page text**, on any failure path. That silently removes the
  entire boundary.

### Included

- an OpenAPI tool server with narrow, versioned, bounded contracts;
- HMAC-signed expiring result handles bound to their issuing search call;
- fetch policy: HTTP(S) only, connect-time address validation, redirect revalidation,
  private/loopback/link-local/cluster/metadata destinations blocked, IPv4 and IPv6;
- size, content-type, concurrency, and time limits;
- input scrub with two rule classes: benign-common constructs stripped, and reject-class
  constructs — Unicode tag block, bidi override, in-word zero-width, adversarially hidden
  text — discarding the **entire source** rather than being cleaned out of it;
- sanitisation run to a fixed point, with non-idempotence itself a rejection signal, so
  strip-once evasions (`<scr<script>ipt>`, normalisation-synthesised sequences) are caught;
- a false-positive corpus of ordinary pages that must survive, because a detector that
  rejects the ordinary web is an outage rather than a control;
- quarantined distillation over LiteLLM on a dedicated budgeted key;
- output scrub, plain text only, fail-closed, with an adversarial corpus and property test;
- a quarantined classifier over the scrubbed distillation returning a closed-enum verdict and
  never an explanation, since a reason string is both a channel and a bypass oracle;
- search brokered so the provider credential never enters OpenWebUI, snippets scrubbed on
  the same path;
- typed failures rather than page text on every error path;
- aggregate metrics without URL, query, question, content, or user labels, with output-scrub
  drops and novel `fetch_url` hosts as alertable security signals.

### Deliberately not included

- research synthesis, findings, citations, provenance, or conflict detection;
- a combined `research()` orchestrator tool;
- a job runner, run lifecycle, or per-request Kubernetes Job;
- durable state, caching, or JavaScript execution;
- PDF or other binary content in the first increment;
- returning raw page text under any condition, including failure;
- a model checking a model;
- any claim of provable security or a CaMeL-equivalent guarantee.

### The bypass paths must be closed, or this is not a boundary

A tool only fires when the model elects to call it. OpenWebUI's native web search and its
URL-attachment flow would otherwise pull raw page text into context without passing through
any of this. 0.3 is therefore not complete until `ENABLE_WEB_SEARCH=false`, no
`WEB_LOADER_ENGINE` is configured, and `open-webui` has no general internet egress — the
network policy is what makes the remaining paths fail closed rather than quietly work.

### Ownership

HearthAI owns the tools, the three stages, the corpora, image, chart, tests, and release
artifacts. `home-ops` owns deployment: tool-server registration, disabling native retrieval,
Bitwarden-backed tokens and keys, a budgeted LiteLLM virtual key, metrics and alerts, and
the network policies.

### Acceptance

1. OpenWebUI answers a current-information question entirely through `hearthfetch` tools.
2. Native web search is off and `open-webui` has no general internet egress.
3. No raw page text reaches the privileged context on any path, success or failure.
4. No URL appears in any tool response, proven by corpus and property test.
5. The privileged model cannot reach a destination of its own choosing through
   `fetch_result`, proven by handle forgery, expiry, and tampering tests.
6. Every input-corpus payload is removed and legitimate visible text survives.
7. Direct and redirected requests cannot reach private, local, cluster, or metadata
   destinations, including under DNS rebinding.
8. No page content, URL, query, question, or distillation appears in a metric label, and
   output-scrub drops are observable.

## 0.4 — Governed MCP

### Purpose

Add broader interoperability after the isolation, untrusted-content, and approval model has been exercised by one constrained capability.

### Included

- admin-approved MCP server registry;
- native Streamable HTTP MCP through OpenWebUI;
- stdio MCP only through an isolated bridge;
- server and tool allowlists;
- assignment to Josh;
- credentials outside model context;
- per-user OAuth where supported;
- durable encryption keys for OAuth connection state;
- audit, revocation, and consequential-action approvals;
- tool output treated as untrusted for shared-memory purposes.

### Acceptance

1. Only an administrator registers MCP servers.
2. Only approved servers and tools are visible and callable.
3. Credentials never enter model-visible prompts or arguments.
4. Consequential operations stop for approval.
5. Revocation blocks the next invocation.
6. Tool output cannot silently modify HearthAI shared memory.
7. Restarting OpenWebUI preserves encrypted OAuth connection state.

## Future — Richer sharing and identity

No near-term version promises:

- HearthAI-managed user accounts;
- invitations or membership acceptance;
- verified store membership;
- household roles or guardianship;
- account recovery;
- user deprovisioning;
- operator-unreadable private memory;
- automatic discovery of other people.

These are introduced only if 0.2 proves capability-based shared stores valuable and exposes concrete limitations.

## Open decisions

1. Should 0.2 tokens be revocable before release?
2. Should read and write capabilities be separate?
3. How should OpenWebUI consume the shared-memory skill semantics: OpenAPI descriptions, a model prompt fragment, or both?
4. Who owns personal memory long term? OpenWebUI is the 0.1 implementation, but permanent ownership versus future HearthAI ownership is unresolved.
5. Which Agent Skills-compatible host proves 0.2 portability first?
6. Which LiteLLM model backs the quarantined distiller and which the classifier, and should `fetch_url` (literal model-composed URLs) ship at all? *(Search provider resolved 2026-09-08: self-hosted SearXNG.)*
7. Which first MCP integration is useful enough to justify 0.4?

## Paused implementation work

An earlier implementation effort targeted portable personal memory before the roadmap was clarified. It is not the current plan.

- Branch: `feat/portable-cross-host-memory`
- Status: paused and unmerged
- Completed and reviewed there: Python 3.14 foundation, Git repository boundary, revocable host identity
- In-progress there: personal-memory storage work
- Instruction: do not merge or resume that branch without reconciling it against this roadmap

The older plan at [`superpowers/plans/2026-08-27-portable-cross-host-memory.md`](superpowers/plans/2026-08-27-portable-cross-host-memory.md) is archived context, not an executable current plan.

## Session recovery

When continuing HearthAI roadmap work in a new session:

1. Read this file first.
2. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for component boundaries.
3. Treat `0.1 → 0.2 → 0.3 → 0.4` as authoritative ordering.
4. Do not infer a prescribed HearthAI persona for 0.1.
5. Treat OpenWebUI personal memory as the near-term implementation, not a permanent ownership decision.
6. Treat HearthAI 0.2 as shareable-memory skill and service work, not personal-memory replacement.
7. Keep rich multi-user identity outside the numbered roadmap.
8. Do not execute the archived personal-memory plan without a new explicit decision.
