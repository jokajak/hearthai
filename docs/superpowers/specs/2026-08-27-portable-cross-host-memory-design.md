# Portable Cross-Host Memory Design

**Status:** Deferred detailed design for roadmap 0.2; not the current implementation target  
**Date:** 2026-08-27  
**First hosts:** Claude Code and OpenWebUI  
**Canonical contract:** OpenAPI 3.x

> Immediate sequencing is governed by [`2026-08-30-capability-roadmap-design.md`](2026-08-30-capability-roadmap-design.md), which starts with the 0.1 authenticated chat gateway.

## Summary

HearthAI's first value proof is cross-host continuity: Josh explicitly saves a memory through one AI host and can retrieve it through another. The first increment does not need semantic retrieval, graph traversal, automatic extraction, household sharing, or workflow orchestration.

The OpenAPI schema is the portable contract. OpenWebUI consumes it directly as a tool server. Claude Code loads a standard Agent Skill that invokes the same HTTP operations through its existing shell capability and `curl`. Both hosts authenticate with independently revocable keys mapped to Josh's principal and personal-memory namespace.

The memory core sits behind the API and ahead of a replaceable storage interface. The current files-and-Git implementation may remain as the first adapter, but it is not a permanent architectural commitment. Neo4j is considered only after real usage produces a recurring graph-shaped query that the simple adapter cannot serve. n8n remains outside the synchronous memory path and is considered only after a repeated asynchronous workflow appears.

The approved boundary diagram and incremental runway are preserved in [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Current-State Findings

The repository contains two different confidence levels about the memory substrate:

- The older [`docs/SPEC.md`](../../SPEC.md) describes OKF bundles, Git versioning, and QMD retrieval as adopted MVP choices. It says Neo4j was considered but lacked a concrete query.
- The current [`README.md`](../../../README.md) explicitly supersedes the older spec where they disagree. It calls files under Git, probably using OKF plus a retrieval layer, the "current guess." It also lists required memory structure as untested.
- n8n is absent from the repository's code, documentation, and history. It was not previously selected or rejected.
- The existing shared-memory slice is real: an Agent Skill, a standard-library Python client, an HTTP service, files with frontmatter, Git-per-write persistence, term-overlap search, a Helm chart, and tests.

Therefore OKF plus Git is provisional, Neo4j is deferred rather than rejected, and n8n is an unevaluated orchestration option rather than a memory-backend candidate.

## Goal

Deliver a self-hosted personal-memory service that proves bidirectional continuity between Claude Code and OpenWebUI while preserving the ability to replace storage, retrieval, orchestration, and host bindings independently.

The first increment succeeds when:

1. Josh explicitly saves a unique memory through Claude Code and recalls the same record through OpenWebUI.
2. Josh explicitly saves a unique memory through OpenWebUI and recalls the same record through Claude Code.
3. Each host uses an independently revocable API key mapped to the same principal.
4. Neither host depends on a HearthAI client library or backend-specific data model.
5. Failure is surfaced honestly; no host claims a failed save succeeded.

## Non-Goals

The first increment does not include:

- automatic memory extraction;
- agent-proposed saves;
- ambient injection of all memory into every prompt;
- embeddings or semantic retrieval;
- graph relationships or multi-hop traversal;
- correction and supersession workflows;
- household sharing or invitations;
- OIDC;
- end-to-end encryption;
- MCP;
- LiteLLM middleware;
- n8n or another workflow engine;
- a Neo4j migration;
- replacement of the current service language or deployment model solely for architectural neatness.

## Architectural Principles

1. **Contract before backend.** Hosts depend on OpenAPI operations and domain records, not storage mechanics.
2. **One semantic contract, thin host bindings.** Hosts may integrate differently; observable behavior remains the same.
3. **Explicit writes first.** HearthAI persists content only after an unambiguous user save instruction.
4. **Identity is derived, not asserted.** The server derives principal, namespace, and source host from the presented key.
5. **Complexity is earned by evidence.** Graph retrieval and workflows respond to observed failures, not architectural intuition alone.
6. **Human-readable export remains possible.** A future backend may be opaque internally, but HearthAI must retain a backend-neutral export path.
7. **Failure is visible.** Hosts never convert service errors into plausible success or empty-memory claims.

## System Boundary

### Canonical OpenAPI contract

HearthAI publishes an OpenAPI 3.x document from a stable service URL. The schema is the source of truth for request shapes, response shapes, authentication, operation identifiers, error envelopes, and tool descriptions.

The first contract exposes four model-facing operations:

| Operation ID | HTTP operation | Purpose |
|---|---|---|
| `health` | `GET /health` | Confirm service availability and version. |
| `saveMemory` | `POST /v1/memories` | Persist one explicitly approved memory. |
| `recallMemories` | `GET /v1/memories/recall?q=&limit=` | Retrieve memories using the first adapter's supported query semantics. |
| `listMemories` | `GET /v1/memories?limit=` | Return recent memories without claiming semantic relevance. |

Backend-specific concepts do not appear in this contract. Prohibited examples include file paths, Git commit IDs, OKF bundle names, graph node labels, Cypher, workflow-run IDs, and Python class names.

### Host bindings

#### Claude Code

The portable Agent Skill contains `SKILL.md` and no executable client. It defines narrow triggers:

- call `saveMemory` only after an explicit instruction such as "remember this";
- call `recallMemories` when Josh explicitly asks what HearthAI remembers or when the answer clearly requires previously stored context;
- report the returned record or error accurately;
- never place API keys in prompts, command arguments that may be logged, or skill content.

The skill invokes the service using Claude Code's existing shell capability and standard `curl`. Configuration supplies the base URL and API key outside the skill.

#### OpenWebUI

OpenWebUI registers HearthAI as an OpenAPI tool server. For the personal first increment, a user tool-server registration is preferred when the browser can reach HearthAI. If the deployment requires backend-originated requests, the same schema may be registered as a global tool server with access restricted to Josh.

The OpenAPI operation descriptions carry the explicit-save and honest-failure policy so the OpenWebUI model sees the same behavioral constraints as Claude Code.

For browser-originated requests, HearthAI permits only the configured OpenWebUI origin through CORS. Wildcard CORS is prohibited.

#### Later hosts

Oh My Pi, Codex, LiteLLM-based applications, and other hosts consume the same OpenAPI semantics through their native extension mechanism. A host adapter may translate transport or registration details, but it may not redefine memory records, authentication identity, save consent, or error behavior.

### Memory core

The memory core owns:

- host-key authentication;
- principal and namespace resolution;
- input validation;
- server timestamps;
- source-host provenance;
- duplicate detection;
- backend-neutral save, recall, and list operations;
- content-free operational telemetry.

The core does not own:

- deciding when a model should save or recall;
- model prompting beyond OpenAPI descriptions;
- file or Git layout;
- graph traversal;
- workflow execution;
- host-specific configuration.

### Storage port

The core calls a backend-neutral storage interface with these semantics:

```text
save(principal_id, content, source_host, created_at)
  -> { memory: MemoryRecord, created: boolean }

recall(principal_id, query, limit)
  -> MemoryRecord[]

list(principal_id, limit)
  -> MemoryRecord[]
```

Every implementation must provide namespace isolation, deterministic duplicate behavior, atomic writes, stable record identifiers, and consistent ordering.

The files-and-Git adapter may use the existing Markdown/frontmatter persistence and term-overlap retrieval. Git remains internal audit and recovery machinery; it is not part of the host contract.

### Workflow boundary

No workflow engine participates in synchronous save, recall, list, authentication, or authorization. The core may later emit backend-neutral memory events after committed writes. n8n or another engine may consume those events only when a real asynchronous workflow justifies the additional component.

## Trust and Identity

### Phase-one trust boundary

The first increment is self-hosted and operator-trusted. Josh's personal memory is centralized so Claude Code and OpenWebUI can access it. The operator can read stored plaintext.

This deliberately relaxes the current README's aspirational invariant that a private store is unreadable by the server operator. The design does not claim end-to-end encryption. It preserves a storage and contract boundary that permits encryption later without making encryption part of the continuity proof.

### Host keys

Each host key has:

```text
HostKey
  key_id          non-secret stable identifier
  key_hash        one-way hash of the secret
  principal_id    Josh's stable principal identifier
  host_id         for example, claude-code or openwebui
  created_at      server timestamp
  revoked_at      nullable server timestamp
```

The secret is generated with cryptographically secure randomness, shown once, and stored only in host configuration. The service compares a hash of the presented key and rejects revoked keys immediately.

Key issuance and revocation occur through an operator-only local administration surface. They are not model-facing OpenAPI operations and are not available through either AI host.

Both first-increment host keys map to the same principal and default personal-memory namespace. A separate fixture principal exists only for isolation testing.

## Memory Record

The first record is intentionally small:

```text
MemoryRecord
  id            opaque stable identifier
  content       non-empty user-approved text
  created_at    server-generated timestamp
  source_host   host identity derived from the API key
```

Clients cannot supply `id`, `created_at`, or `source_host` as trusted fields.

The first increment does not add tags, projects, arbitrary metadata, embeddings, relationships, supersession, or workflow state. These additions require an observed use case and a compatible contract extension.

## Request and Response Semantics

### Save

Request:

```json
{
  "content": "HearthAI's portable contract is OpenAPI."
}
```

New record: `201 Created`.

```json
{
  "memory": {
    "id": "mem_opaque",
    "content": "HearthAI's portable contract is OpenAPI.",
    "created_at": "2026-08-27T16:00:00Z",
    "source_host": "claude-code"
  },
  "created": true
}
```

Identical content in the same namespace returns `200 OK`, the original record, and `"created": false`. Duplicate detection preserves the original timestamp and source host.

### Recall

`GET /v1/memories/recall?q=portable+contract&limit=10`

```json
{
  "memories": [
    {
      "id": "mem_opaque",
      "content": "HearthAI's portable contract is OpenAPI.",
      "created_at": "2026-08-27T16:00:00Z",
      "source_host": "claude-code"
    }
  ]
}
```

The files-and-Git adapter may use term overlap. The operation does not promise semantic similarity, graph traversal, or current-fact resolution.

### List

`GET /v1/memories?limit=10` returns the most recent records in descending creation order.

### Health

`GET /health` is unauthenticated and returns only service status and version:

```json
{
  "status": "ok",
  "version": "service-version"
}
```

### Authentication

All `/v1/memories` operations use:

```text
Authorization: Bearer <host-key>
```

The key never appears in URL paths or query parameters.

## Data Flows

### Claude Code save to OpenWebUI recall

1. Josh explicitly asks Claude Code to remember text.
2. The Agent Skill invokes `saveMemory`.
3. HearthAI authenticates the Claude Code key and resolves Josh's namespace.
4. The core derives source host, timestamps the record, and calls the storage port.
5. The adapter atomically persists the record.
6. The API returns the complete record; Claude Code confirms the saved text.
7. Josh asks OpenWebUI to recall the memory.
8. OpenWebUI invokes `recallMemories` using its own key.
9. HearthAI resolves the same principal and namespace and returns the record.

### OpenWebUI save to Claude Code recall

The same flow runs in reverse. The stored record's `source_host` is `openwebui`, proving that identity is derived per host while the namespace is shared per principal.

## Error Model

Every error response uses a stable envelope:

```json
{
  "error": {
    "code": "invalid_api_key",
    "message": "The API key is missing, invalid, or revoked."
  }
}
```

| Status | Code | Meaning |
|---|---|---|
| `400` | `invalid_request` | Empty content, malformed query, unsupported field, or invalid limit. |
| `401` | `invalid_api_key` | Key is missing, unknown, malformed, or revoked. |
| `403` | `forbidden` | An authenticated principal attempts another namespace. |
| `503` | `storage_unavailable` | The adapter cannot complete the operation. |

An empty recall is `200 OK` with `"memories": []`.

A failed save creates no partial record and returns no plausible `MemoryRecord`. Host instructions require an explicit failure statement and prohibit success wording after a non-success response.

## Security Requirements

- HTTPS is mandatory outside loopback.
- API secrets are never stored in skill files, prompts, URLs, memory records, Git history, or logs.
- Only key hashes are stored by the service.
- Revocation takes effect on the next request.
- Principal, namespace, and source host are derived from the key.
- Browser-originated OpenWebUI calls use an explicit CORS allowlist.
- Operational logs omit request content and response content.
- A key cannot enumerate principals or namespaces.
- The first-increment administration surface is local and operator-only.
- The design makes no end-to-end-encryption claim.

## Observability

For every model-facing operation, record:

- timestamp;
- operation ID;
- non-secret key ID;
- principal ID;
- source host;
- HTTP status;
- latency;
- result count;
- whether a save created a record or returned a duplicate.

Do not record API keys, request bodies, queries, memory content, prompts, or returned memory content.

The initial evidence set is operational rather than algorithmic: tool-call failures, empty recalls, latency, duplicate frequency, and differences between hosts. A short manual friction log captures cases where a host failed to invoke the appropriate operation.

## Incremental Development

### Increment 1 — Contract and trust

- Define and serve the OpenAPI document.
- Add host-key records, local issuance, and revocation.
- Map the Claude Code and OpenWebUI keys to Josh's principal.
- Place the memory core and storage port in front of the current persistence behavior.
- Preserve existing shared-memory behavior unless an approved implementation plan explicitly migrates it.

**Usable end state:** direct HTTP clients can save, recall, and list Josh's personal memory with revocable credentials.

### Increment 2 — Thin host bindings

- Package the script-free Agent Skill for Claude Code.
- Register the OpenAPI server in OpenWebUI.
- Configure secrets outside prompts and skill files.
- Verify both hosts against the same deployed service.

**Usable end state:** both hosts can explicitly save and recall through their normal interfaces.

### Increment 3 — Real use

- Run both bidirectional acceptance scenarios.
- Use explicit saves in normal conversations.
- Review operational telemetry and the manual friction log.

**Usable end state:** cross-host continuity is part of daily use, and failures produce evidence rather than speculation.

### Increment 4 — Evidence-backed evolution

Improve the smallest failing layer:

- adjust skill/tool descriptions when hosts fail to invoke operations;
- improve the storage adapter when persistence or simple retrieval fails;
- add semantic retrieval only after captured paraphrase misses justify it;
- add graph storage only after a recurring relationship query requires multi-hop traversal or graph updates;
- add n8n only after a repeated asynchronous workflow requires scheduling, retries, external integrations, or human approval;
- consider end-to-end encryption as a separate architecture project when operator-unreadable storage becomes a current requirement.

No later component is an automatic milestone.

## Backend Escalation Gates

### Graph backend

A graph backend is considered only when all of these are available:

1. a captured recurring user query that requires relationships or traversal;
2. evidence that the current adapter cannot answer it correctly or maintainably;
3. a backend-neutral representation of the required relationship semantics;
4. a migration and export path that does not change host contracts;
5. a comparison showing the graph implementation materially improves the captured case.

Neo4j is one candidate after the gate, not the gate itself.

### Workflow engine

A workflow engine is considered only when all of these are available:

1. a repeated asynchronous workflow observed in real use;
2. a requirement for scheduling, retries, external state, fan-out, or human approval;
3. a reason the behavior does not belong in synchronous memory operations;
4. an event contract that does not expose storage internals;
5. failure handling that cannot silently lose or duplicate work.

n8n is one candidate after the gate, not part of memory persistence.

## Verification Strategy

### Contract tests

- Every response validates against the OpenAPI schema.
- Unsupported request fields and invalid limits return `400 invalid_request`.
- All `/v1/memories` endpoints require bearer authentication; `/health` exposes only status and version.
- No backend-specific field appears in the schema.

### Storage-adapter conformance

Every adapter passes the same suite:

- save a new record;
- save identical content and receive the original record;
- recall matching content within one principal namespace;
- list records in descending creation order;
- prevent cross-principal reads;
- survive an injected write failure without a partial record;
- preserve stable identifiers across process restart.

### Authentication tests

- Claude Code and OpenWebUI keys resolve to Josh's principal.
- `source_host` differs according to the key used.
- Revoking the Claude Code key makes its next request return `401`.
- Revoking one key does not affect the other.
- A fixture principal cannot retrieve Josh's records.

### Real-host acceptance

1. Save a unique marker through Claude Code; recall the same ID and content through OpenWebUI.
2. Save a different unique marker through OpenWebUI; recall the same ID and content through Claude Code.
3. Disable the storage adapter; confirm each host reports failure and neither claims the memory was saved.
4. Confirm OpenWebUI loads the actual service schema and Claude Code uses the installed script-free skill.

### Portability audit

- The Agent Skill contains no executable client.
- The skill requires no third-party library.
- The OpenAPI contract is valid independently of Claude Code and OpenWebUI.
- Host-specific configuration contains no storage-backend concepts.

## Compatibility with Existing Shared Memory

The existing `shared-memory` skill and `/stores` API prove useful persistence, network deployment, duplicate handling, and skill packaging. They do not provide the approved personal-memory identity model because access is an unrevocable bearer capability and attribution is self-asserted.

The implementation plan must choose a clean internal structure that reuses proven persistence behavior without pretending current store tokens are host identity. Existing shared-memory behavior remains intact unless the plan includes an explicit caller migration and preserves its observable contract.

## Approved Decisions

- The first value proof is cross-host memory accessibility.
- The first hosts are Claude Code and OpenWebUI.
- Writes are explicit only.
- OpenAPI is canonical.
- Claude Code uses a script-free Agent Skill and standard HTTP through its existing shell capability.
- OpenWebUI consumes the OpenAPI tool server directly.
- Host keys are independently revocable and map to one principal.
- Phase one uses a self-hosted, operator-trusted personal store.
- The first `MemoryRecord` contains only `id`, `content`, `created_at`, and `source_host`.
- Files and Git may remain the first storage adapter but are not permanent commitments.
- Neo4j and n8n are evidence-gated later candidates.
- MCP, LiteLLM middleware, automatic extraction, and end-to-end encryption are outside the first increment.
