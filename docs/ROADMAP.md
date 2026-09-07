# HearthAI Roadmap

**Status:** authoritative capability roadmap<br>
**Last updated:** 2026-09-07<br>
**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)<br>
**Design detail:** [`superpowers/specs/2026-08-30-capability-roadmap-design.md`](superpowers/specs/2026-08-30-capability-roadmap-design.md)

## Direction

HearthAI begins by adopting OpenWebUI as a useful chat platform, then adds HearthAI's distinctive shared-memory skill and service, then takes ownership of web fetching so untrusted pages are retrieved and neutralised away from a credentialed process, then governed MCP interoperability.

The near-term roadmap does **not** build a custom web UI, prescribe a HearthAI persona, replace OpenWebUI's personal memory, or introduce HearthAI user accounts.

```text
0.1 OpenWebUI foundation
  ↓
0.2 HearthAI shareable memory
  ↓
0.3 brokered web fetch with hearthfetch
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
| Web fetch | HearthAI `hearthfetch` service | Retrieving untrusted pages away from a credentialed process, fetch policy, sanitising page text, marking content as untrusted data, and brokering the search provider |
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

## 0.3 — Brokered web fetch with `hearthfetch`

### Purpose

Make HearthAI the single broker for every web fetch OpenWebUI performs on untrusted
content, so page retrieval happens somewhere with no credentials and no useful network
position, and so page text is stripped of injection carriers and marked as data before it
reaches a model context.

### What this does and does not do

Brokering fetches **does not stop prompt injection.** The text still arrives in the
model's context — delivering it is the point of the call. What changes is where the fetch
runs and what that process can reach. State this correctly wherever the capability is
described.

The three layers that do reduce injection risk, in descending order of effect:
constraining what the model can do after it reads (chat and tool configuration, outside
this release); stripping hidden text, comments, metadata, and invisible Unicode from the
page (`hearthfetch`); and marking the content as untrusted data (`hearthfetch`).

### Architecture

OpenWebUI already has the plug-in point. `WEB_LOADER_ENGINE=external` and
`WEB_SEARCH_ENGINE=external` redirect its page fetches and search-provider calls to
`hearthfetch`, which implements those two endpoints and returns sanitised text.

`hearthfetch` is stateless: no run records, no registry, no durable storage, no
per-request Kubernetes object, no lifecycle. A request is answered or it fails. The
isolation claim is enforced by a `home-ops` network policy that denies `open-webui`
general egress — not by anything `hearthfetch` asserts about itself.

### Included

- the two OpenWebUI `external` endpoints, implemented to the deployed version's contract;
- bearer authentication, strict request validation, unknown-field rejection;
- fetch policy: HTTP(S) only, connect-time address validation, redirect revalidation,
  private/loopback/link-local/cluster/metadata destinations blocked for IPv4 and IPv6;
- size, content-type, concurrency, and time limits, enforced during and after streaming;
- a sanitiser removing scripts, styles, comments, hidden and off-screen elements,
  attribute-borne and `<meta>` text, and invisible or bidi Unicode;
- an adversarial corpus with one page per sanitiser rule, asserting both that payloads are
  removed and that visible text survives;
- an untrusted-content envelope around returned page text;
- search brokered so the provider credential never enters OpenWebUI, with snippets
  sanitised on the same path;
- per-URL failures omitted from the batch rather than failing the conversation;
- aggregate metrics without URL, query, content, or user labels.

### Deliberately not included

- research synthesis, findings, citations, provenance, or conflict detection;
- a job runner, run lifecycle, or per-request Kubernetes Job;
- durable state, caching, or JavaScript execution;
- PDF or other binary content in the first increment;
- any general execution surface.

### Ownership

HearthAI owns the service, sanitiser, fetch policy, image, chart, tests, and release
artifacts. `home-ops` owns the deployment: OpenWebUI's `WEB_*` configuration,
Bitwarden-backed tokens and the search key, metrics collection, and the network policies —
default-deny egress on `open-webui`, and ingress-from-`open-webui`-only on `hearthfetch`
with no path to `hearthmem`, LiteLLM, or the Kubernetes API.

### Acceptance

1. OpenWebUI performs a web search and loads a pasted URL entirely through `hearthfetch`.
2. `open-webui` has no general internet egress and the retrieval path still works.
3. Every adversarial corpus payload is absent from what reaches a model context, and
   legitimate visible page text survives.
4. Returned page text is enveloped and marked as untrusted data.
5. Direct and redirected requests cannot reach private, local, cluster, or metadata
   destinations, including under DNS rebinding.
6. Blocked, oversized, unreachable, or unsupported URLs are omitted and logged without
   failing the batch; search-provider failure returns an empty result.
7. No page content, URL, query, or user identifier appears in a metric label.
8. The service holds no durable state and no credential beyond its own tokens and the
   search-provider key.

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
6. Which search provider should `hearthfetch` broker for 0.3, and should the untrusted-content envelope be on by default?
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
