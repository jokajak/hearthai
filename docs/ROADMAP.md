# HearthAI Roadmap

> **Proposed safer fetch (2026-09-12):** Add an isolated URL fetch tool with strict response rejection and YARA inspection. See the [implementation plan](superpowers/plans/2026-09-12-isolated-webfetch.md). This proposal does not change the existing capability ordering or implement web research.

**Status:** authoritative capability roadmap<br>
**Last updated:** 2026-09-09<br>
**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)<br>
**Design detail:** [`superpowers/specs/2026-08-30-capability-roadmap-design.md`](superpowers/specs/2026-08-30-capability-roadmap-design.md)

## Deployment trust model

HearthAI is intended to run locally in a private environment with a trusted server operator.
The operator can access stored memory and conversations. Operator-unreadable memory and privacy
isolation between mutually untrusted tenants are out of scope, not deferred release requirements.
Personal context remains separate between users in normal application use, and shared writes
still require approval of the exact content and destination.

This trust applies to the operator, not to models, tools, repository code, or fetched content.
Authentication, scoped access, credential protection, sandboxed execution, and action approvals
remain required. Local deployment does not require local inference: a configured hosted model
provider still receives the context sent to it.

## Cohesive deployment — 2026-09-09

Open WebUI is part of the HearthAI deployment. `deploy/charts/hearthai` packages it
with LiteLLM, Meridian, optional default-on single-instance Postgres, and hearthmem; home-ops supplies environment inputs and a release pin.
The proxy image and catalogue are imported unchanged from the known-working home-ops
configuration, with automatic internal Open WebUI routing. Future
shared-memory adapter/tool registration and `ai-jobs` runtime wiring belong in this
package. The current chart does not claim those pending capabilities are available.
See [`deploy/README.md`](../deploy/README.md) for the deployment contract.

## Current priority correction — 2026-09-08

This section supersedes the earlier *Direction*, numbered ordering, and 0.1 exclusions below until the milestone detail is rewritten.

HearthAI's first proof of value is a useful OpenWebUI work surface, not an isolated memory experiment. The delivery order is:

1. **Git change work first:** support any repository that carries both an explicit HearthAI authorization record and a GitHub App installation selection. A dedicated HearthAI GitHub App and short-lived installation tokens make GitHub activity attributable to `hearthai[bot]`, never Josh. The agent may create a branch, make changes, run the repository's own checks, and open a pull request; it cannot merge. Those checks are repository-controlled code, so what contains them is the sandbox described in *Execution substrate* below, not an allowlist of check names. The `ai-jobs` control plane owns the durable run audit.
2. **Bounded web research:** make current, source-backed research available from the same chat surface through an isolated, fixed-purpose worker. It is not a generic executor.
3. **Automatic topic organization:** topic changes create a clean logical conversation by default. Detection belongs to a HearthAI topic manager at the chat boundary, not to the answering model, and the boundary defaults closed: a false split costs a re-selected artifact, never lost history. Preserve no transcript unless an explicit artifact, named project/repository, or short relevant task brief is selected. Old topics remain linked for navigation but do not contaminate the next prompt. Presenting split topics as navigable is chat-surface behavior OpenWebUI does not provide, so this is the one capability that may require relaxing the *Direction* non-goal of building no custom web UI — through an OpenWebUI extension if one suffices, otherwise by an explicit recorded decision.
4. **Shareable memory:** integrate the existing skill and service where it improves these workflows. Sharing remains deliberate and shared writes still require approval.
5. **Governed MCP and richer household identity:** follow only after the above boundaries have real-world evidence.

OpenWebUI remains the browser surface and its native memory remains the near-term personal-memory implementation. This correction changes product priority, not the existing commitments to model-independent boundaries, deliberate sharing, least privilege, provenance, and approval for consequential actions.

### Execution substrate

Every model-callable capability runs as a fixed set of short-lived, sandboxed Kubernetes pods,
each with a profile-fixed image, no durable filesystem, no Kubernetes API access, no
service-account token, and no ambient credentials. The profile fixes how many pods a run creates,
what each may reach, and its egress and resource limits; the caller never does. The `ai-jobs`
control plane accepts only a typed request and selects a reviewed worker profile; it is not a
general job API.

The first worker profile is **Git change work**. It validates the pod manager rather than bypassing
it: a materializer init container uses a short-lived, read-only repository token to create an
ephemeral workspace, removes the token and Git credential/configuration before the worker starts,
and drops `.git` unless a fixed profile demonstrably needs it. A profile that does need history
must receive the credential out of band — an `http.extraHeader` or an equivalent — rather than in
the remote URL, because deleting `.git/config` keys alone leaves token residue in `FETCH_HEAD`,
`packed-refs`, and the reflog, all readable by the untrusted check code sharing that workspace.
The sandboxed worker edits the workspace and executes repository-controlled checks. After it
terminates, `ai-jobs` starts a **separate publisher pod**, not a sidecar: it fetches the
repository afresh, applies the worker's captured change artifact without executing repository
code, and uses a short-lived, repository-scoped installation token to create the branch, commits,
and PR as `hearthai[bot]`. The App private key never enters a pod, and no worker can reach the
publisher over a shared pod network or volume. Commit content is worker-controlled by design;
containment is the branch, lack of merge authority, separate publisher boundary, and audit record.
No general tools are installed or exposed in the initial worker beyond what that fixed profile
requires.

Repository authority requires **both** an explicit HearthAI authorization record and a selected
repository in the GitHub App installation. Removing either blocks the next run. The `ai-jobs`
control plane owns the durable run audit: request, selected profile, authorization decision,
approval, pod identity, repository/ref, produced commits, checks, and GitHub actor.

## Earlier roadmap detail — pending rewrite

Everything from **Direction** through **Future — Richer sharing and identity** below is superseded by the correction above until it is rewritten.

**Open decisions**, **Paused implementation work**, and **Session recovery** are *not* superseded. In particular, the standing instruction not to merge or resume `feat/portable-cross-host-memory` without reconciliation remains in force.

## Direction

HearthAI begins by adopting OpenWebUI as a useful chat platform, then adds HearthAI's distinctive shared-memory skill and service, then delegated web research, then governed MCP interoperability.

The near-term roadmap does **not** build a custom web UI, prescribe a HearthAI persona, replace OpenWebUI's personal memory, or introduce HearthAI user accounts.

```text
0.1 OpenWebUI foundation
  ↓
0.2 HearthAI shareable memory
  ↓
0.3 delegated web research with ai-jobs
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
| Web research | HearthAI `ai-jobs` service | Authorization, ephemeral worker orchestration, bounded search and fetch, result delivery, provenance, and aggregate observability |
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

## 0.3 — Delegated web research with `ai-jobs`

### Purpose

Let OpenWebUI transparently delegate current or deeper web research to HearthAI and receive a source-backed result without exposing infrastructure concepts to the user.

### Architecture

`ai-jobs` is an independently deployable HearthAI control-plane service. For every request, it creates one Kubernetes Job running the fixed web-research worker. The worker performs a bounded research loop through job-scoped LiteLLM, search, and fetch capabilities brokered by the control plane, then submits a structured result.

Research is synchronous in 0.3: it either returns a terminal result within the OpenWebUI tool request or fails as `deadline_exceeded`. The worker deadline is shorter than the OpenWebUI tool timeout so the control plane has time to validate and return the terminal response. OpenWebUI users never manage job identifiers, polling, Kubernetes resources, or a separate dashboard.

### Included

- a model-facing web-research contract containing research intent and bounded options only;
- caller authentication and authorization;
- durable internal execution state and terminal result storage;
- one isolated, ephemeral Kubernetes Job per research execution;
- a fixed web-research worker image, command, privileges, and resource policy;
- worker-scoped authorization with provider credentials retained by `ai-jobs`;
- a bounded research loop using LiteLLM plus constrained search and page fetch;
- public HTTP/HTTPS fetches only;
- no Kubernetes service-account token, host mount, private volume, durable worker filesystem, local credentials, or browser session;
- private, loopback, link-local, cluster, and cloud-metadata destinations blocked;
- DNS and redirect revalidation;
- response-type, size, and timeout limits;
- structured synthesis, findings, evidence, source URLs, conflicts, uncertainty, and limitations;
- synchronous completion within the OpenWebUI tool timeout, with bounded worker and response-delivery margins;
- stable failure categories that do not expose Kubernetes details;
- health, readiness, and aggregate operational metrics without sensitive labels;
- fetched content treated as untrusted evidence;
- audit events without full fetched content, research questions, URLs, credentials, or other secrets;
- explicit approval before web-influenced writes to HearthAI shared memory.

### Ownership

HearthAI owns the contracts, control plane, worker behavior, container images, Helm chart, tests, and release artifacts. `home-ops` owns the namespace and deployment wiring, namespace-scoped RBAC, storage, network policy, Bitwarden-backed secret delivery, OpenWebUI tool registration, version pins, and health and metrics collection.

General execution remains explicitly out of scope. No model-facing interface accepts images, commands, environment variables, credentials, pod configuration, shell operations, filesystem operations, package installation, process management, Docker access, or arbitrary sockets.

### Acceptance

1. OpenWebUI invokes web research from a normal conversation and receives a validated result with synthesis, evidence, citations, conflicts, and limitations.
2. HearthAI creates one new worker Job per execution, and the worker terminates and is cleaned up according to retention policy.
3. Research completes synchronously within the configured tool timeout or fails as `deadline_exceeded`, without asking the user to copy a job ID, poll, or operate Kubernetes.
4. Direct and redirected requests cannot reach private, local, cluster, or metadata destinations.
5. Oversized, binary, unsupported, malformed, provider-failed, and timed-out work fails explicitly through stable categories.
6. Worker pods contain neither provider credentials nor Kubernetes credentials and have no durable state.
7. Fetched instructions cannot expand worker authority, invoke general tools, or write shared memory without approval.
8. Health and aggregate metrics answer operational questions without user, conversation, question, URL, citation, credential, or job-ID labels.
9. The model cannot access a shell, filesystem, arbitrary socket, package installer, process manager, Docker tool, or general Kubernetes Job API.

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
- automatic discovery of other people.

These are introduced only if 0.2 proves capability-based shared stores valuable and exposes concrete limitations.

## Open decisions

1. Should 0.2 tokens be revocable before release?
2. Should read and write capabilities be separate?
3. How should OpenWebUI consume the shared-memory skill semantics: OpenAPI descriptions, a model prompt fragment, or both?
4. Who owns personal memory long term? OpenWebUI is the 0.1 implementation, but permanent ownership versus future HearthAI ownership is unresolved.
5. Which Agent Skills-compatible host proves 0.2 portability first?
6. Which search provider and bounded research defaults should `ai-jobs` use for 0.3?
7. Which first MCP integration is useful enough to justify 0.4?
8. How is repository authorization revoked, and what happens to a run already in flight when either the HearthAI authorization record or the GitHub App installation selection is removed?
9. Which fixed profiles, if any, may retain `.git`, and how is credential residue shown to be absent when one does?
10. Does the topic manager require a chat surface beyond OpenWebUI, and if so does that relax the no-custom-frontend non-goal or defer the capability?

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
3. Treat the **Current priority correction** as the authoritative ordering; the numbered `0.1 → 0.2 → 0.3 → 0.4` detail is pending rewrite.
4. Do not infer a prescribed HearthAI persona for 0.1.
5. Treat OpenWebUI personal memory as the near-term implementation, not a permanent ownership decision.
6. Treat HearthAI 0.2 as shareable-memory skill and service work, not personal-memory replacement.
7. Keep rich multi-user identity outside the numbered roadmap.
8. Do not execute the archived personal-memory plan without a new explicit decision.
