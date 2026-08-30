# HearthAI Roadmap

**Status:** authoritative capability roadmap  
**Last updated:** 2026-08-30  
**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)  
**Design detail:** [`superpowers/specs/2026-08-30-capability-roadmap-design.md`](superpowers/specs/2026-08-30-capability-roadmap-design.md)

## Direction

HearthAI begins by adopting OpenWebUI as a useful chat platform, then adds HearthAI's distinctive shared-memory skill and service, then safe external-information access, then governed MCP interoperability.

The near-term roadmap does **not** build a custom web UI, prescribe a HearthAI persona, replace OpenWebUI's personal memory, or introduce HearthAI user accounts.

```text
0.1 OpenWebUI foundation
  ↓
0.2 HearthAI shareable memory
  ↓
0.3 sandboxed web search and fetch
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
| Web access | HearthAI capability service | Sandboxing, network policy, provenance, taint |
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

## 0.3 — Sandboxed web search and fetch

### Purpose

Give HearthAI current external information without general execution or trusted-content assumptions.

### Preferred implementation hypothesis

Use a dedicated Open Terminal container as the execution substrate, behind a custom HearthAI tool facade that exposes only web-research operations.

OpenWebUI does **not** receive Open Terminal's general `run_command`, file-write, package-install, process-management, or Docker tools in 0.3.

### Included

- one dedicated `web-research` Open Terminal environment;
- custom image or startup configuration containing only required search/fetch tooling;
- narrow OpenAPI or MCP facade exposing `search_web` and `fetch_url`;
- Open Terminal API key held by the facade, outside model context;
- egress firewall and public HTTP/HTTPS only;
- no host Docker socket, private volumes, local credentials, or browser sessions;
- private, loopback, link-local, cluster, and cloud-metadata destinations blocked;
- DNS and redirect revalidation;
- response-type, size, and timeout limits;
- extracted content plus source provenance;
- fetched content marked untrusted;
- audit events without fetched content or secrets;
- explicit approval before web-influenced writes to HearthAI shared memory.

### Acceptance

1. HearthAI fetches and cites an allowed public page.
2. Direct and redirected requests cannot reach internal or metadata addresses.
3. Oversized, binary, unsupported, and timed-out responses fail explicitly.
4. Fetched instructions cannot invoke tools or write shared memory without approval.
5. The model cannot access a shell, filesystem, arbitrary socket, package installer, process manager, or general Open Terminal command tool.

## 0.4 — Governed MCP

### Purpose

Add broader interoperability after the sandbox, provenance, and approval model has been exercised by one constrained tool.

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
6. Is Open Terminal the final 0.3 substrate, and should the narrow facade use OpenAPI or MCP?
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
