# Personal AI Infrastructure — Validated Behavioral Specification

Status: interview-validated, pre-design. This captures **what the system does and how it behaves**, not how it is implemented. Decisions marked ⏸ are explicitly deferred; ⚠ marks known risks accepted with eyes open.

> The [README](../README.md) is the implementation-side view of this document: the same behavior, decomposed into the six components that will carry it. Section numbers here are stable — the README links to them.

---

## 1. Vision

A single household AI entity ("the orchestrator") with persistent, incrementally improving memory, reachable through multiple modalities, serving each family member through isolated memory spaces, and acting on the world only through constrained, spawned subagents. The orchestrator itself is deliberately low-privilege: its effective power comes from what it can spawn, and that is where control is applied.

## 2. Modalities

| Modality | Behavior |
|---|---|
| **Phone/web chat** | Always-on. Each family member authenticates and talks to the entity scoped to their identity. |
| **Terminal session** | Opening a terminal on the laptop connects it as a client of the cluster orchestrator and **registers the laptop as an execution endpoint for the duration of the session**. Same entity, borrowed hands. Presence is the approval; hands are revoked when the session ends. |

It is one entity across modalities: work done at the terminal Tuesday is known to the phone chat Thursday (subject to space rules).

## 3. Identity & Access

- **Authentik (already deployed) fronts everything via OIDC.** Group claims (`adults`, `kids`, per-user) map to memory spaces.
- Identity arrives at the orchestrator as a **verified claim, never a chat-asserted fact**. Kid isolation is enforced at the API layer by group membership, not by prompt.

## 4. Memory Architecture

**Model: space-based partitioning (fail-closed), file-based storage, git-versioned.**

- **Format: OKF bundles** — markdown + YAML frontmatter, self-describing, `cat`-able, diffable. One bundle per space.
- **Retrieval: QMD** over the bundles. No vector/graph database in MVP. ⏸ Add real retrieval infrastructure only when retrieval demonstrably misses; Neo4j was considered and could not be justified by a concrete query.
- **Spaces (MVP): Josh's personal store + household shared store.** Spouse's store, kid-safe store, and per-kid stores follow the same pattern post-MVP. ⏸
- A kid's agent reads **only** the kid-safe store — no query-time filtering of richer stores. Sparse and stale shared context is accepted; promotion-friction reduction is future work. ⏸
- `log.md` per bundle provides the audit trail; git history provides revert.

### 4.1 Write policy (reconciled)

1. **Own partition: the agent writes autonomously.** Inspectability is achieved through git post-hoc review and revert, not pre-approval queues.
2. **Cross-space promotion (e.g., personal → household): human-approved, always.** The agent may suggest promotion; it may never perform it.
3. **Web-tainted sessions cannot write memory without human approval** (see §7).
4. Explicit "remember this" from a user is honored as a normal own-partition write.

## 5. Personas

**A persona = system prompt + the data stores available for context. Not a separate agent, not private memory.** Same brain, different direction.

> **Revised after the interview** (see the [README](../README.md#a-persona-is-a-system-prompt-plus-the-stores-you-already-hold)). This section originally read "prompt + scoped memory view … different slice of the graph," with per-persona read partitioning. Two corrections: personas come in a **personal** and a **shared** kind, and a persona **directs** a conversation rather than restricting what it may read. The original wording is preserved in git history.

- **Personal personas** serve one person; **shared personas** (home manager, household chief-of-staff) are addressable by every member and directed at household-level concerns. Both run as the member addressing them.
- Domain roster within a person's personas: medical advisor, financial advisor, life advisor.
- The orchestrator knows the roster and routes conversations to the appropriate persona.
- **A persona reads every store the member holds** — their own, plus the household store. Read-gating by persona would only degrade answers: it is the same human, in the same session, with the same rights. The sole access boundary is *between people*, enforced on verified identity.
- **Per-persona tagging within the person's bundle** (frontmatter): every write lands in the session member's own store carrying the tag of the persona that produced it. Tags are provenance — attribution, promotion candidacy, revert granularity, relevance ordering — and are never consulted for access.
- **The household store has no autonomous writer.** The only path into it is human-approved promotion (§4.1.2), whichever persona was being addressed.
- ⚠ **Parental visibility rule (not admin visibility):** visibility follows the guardianship relationship, not system operation. Parents have insight into their children's persona conversations, including medical. **Adults' partitions are private to each adult** — operating the infrastructure grants no read access to a spouse's space. This is a deliberate family-policy decision, accepted with the stated commitment to treat that access appropriately; it should be disclosed to household users rather than silent, and it naturally sunsets as children reach adulthood.

## 6. Orchestrator & Subagents — Security Model

**Principle: privilege separation, SELinux-style. The orchestrator has minimal direct tool/network access. All hands are subagents.**

- Orchestrator spawns **ephemeral subagents constrained by compiled policy** (analogous to domain transitions). Runtime enforces each profile's constraints — this is defense in depth, not the sole control.
- **Policy authoring (adopted from IronCurtain): plain-English constitution compiled into enforceable allow/escalate/deny rules** — no DSL, no static YAML catalog. This applies the project's derivation instinct to security policy itself. Per-space constitutions are possible (a kid's constitution is stricter than an adult's).
- **Trust mechanism (adopted from IronCurtain, inverted taint): direct human input is *blessed*; everything else — web content, file content, message content — is unblessed by default.** Escalated operations auto-approve only when blessed intent covers them; unblessed context forces escalation to a human. Same trust lattice as §7, cleaner enforcement point.
- **Tool-result translation layer (context economy): raw MCP JSON never enters inference context.** MCP is retained as the standardization layer for system access, but the runtime projects every tool result before it reaches any model: (1) deterministic per-tool projections extracting only task-relevant fields into terse text — the default; (2) CaMeL-style execution where results live as runtime variables and only explicitly surfaced values reach the model — the full payload never enters context; (3) quarantined-LLM summarization only as a last resort, and its output must carry provenance labels (this is the §11.1 leak point). The reference monitor and the translation layer are the same interception boundary: label it, then compact it. Rule of thumb: *project before inject* — the waste is unprojected payloads, not structure itself.
- Two subagent classes:
  - **Ephemeral tool-runners** — instantiated from the catalog, scoped, discarded.
  - **Persistent persona subagents** — see §5 (though behaviorally they are views, not independent agents).
- ⏸ **Profile proposal:** the orchestrator may eventually propose new subagent profiles for one-time human approval and reuse. Not MVP.
- ⚠ **Confused-deputy residual risk:** the orchestrator's effective privilege is the union of spawnable profiles. The catalog is the policy boundary; keep it small and reviewed.

## 7. Trust Flow — "trust flows down, never up"

Motivating threat: **prompt injection from arbitrary web content.**

1. **Read-only web subagents spawn without approval**, but their output returns to the orchestrator as **tainted data**. A session that has ingested web content cannot trigger memory writes or spawn write-capable subagents without human approval.
2. **External write actions** (send email, post, purchase) — **human-approved every time. No allowlist in MVP.**
3. **Local execution on the laptop** — approved per-session by opening the terminal; capability ends with the session.

## 8. Inference Tiering

**Honest framing: cloud-primary with opportunistic local offload.**

- Always-on services run on the home Kubernetes cluster (underpowered for serious inference).
- Cloud APIs are the primary brain; the laptop serves as an inference endpoint when it happens to be on.
- ⚠ **Accepted risk:** medical/financial conversations transit cloud providers for now. Routing is by *availability*, not data sensitivity.
- ⏸ Sensitivity-based tiering (local-only processing for designated data classes) is a stated future goal and should not be foreclosed by MVP design.

## 9. MVP Scope

**In:**

- Cluster-hosted orchestrator + web chat, Authentik OIDC in front
- Two OKF bundles: Josh personal, household shared; QMD retrieval; git versioning
- Autonomous own-partition memory writes; human-gated promotion; web-taint write gate
- Predefined subagent profile catalog; read-only web subagent; taint tracking
- Terminal-as-registered-execution-endpoint session model
- Persona routing with scoped memory views (initial roster, Josh's partition only)

**Out (deferred):** spouse/kid spaces and onboarding, promotion-friction UX, profile proposal workflow, sensitivity-based inference tiering, vector/graph retrieval, external write allowlists.

## 10. Prior Art & Positioning

**Decision: steal or incorporate — do not adopt either as the substrate.** Neither provides the load-bearing requirement: identity-mapped household multi-tenancy.

| Source | What it validates | What we take | What it lacks |
|---|---|---|---|
| **IronCurtain** (Provos) | The §6/§7 security model exists and works: policy-sandboxed MCP tool calls, allow/escalate/deny | Constitution-compiled policy; blessed-input trust mechanism; honest "limit clearly-unintended damage" framing for §11 | Multi-tenancy, identity mapping, memory model |
| **Hermes Agent** (Nous) | §2 same-entity-across-surfaces is achievable: one agent/one memory across Telegram, Signal, CLI, etc.; gateway for unattended scheduled runs; sandbox-backend abstraction (incl. SSH → laptop-as-endpoint) | Surface-gateway architecture pattern; subagent isolation pattern; SSH sandbox backend idea | Single-user to its core; no space partitioning, no OIDC identity, opaque memory vs OKF |

**IronCurtain-as-component stays open**: it deliberately wraps standard, unmodified MCP servers, so using it as the enforcement runtime under our own orchestrator (option c) is compatible with this decision and should be evaluated during design, not now.

**What this project uniquely is:** the household layer — OIDC-verified identity mapped to fail-closed memory spaces, guardian-of visibility semantics, personas as scoped views, and human-legible OKF memory. Everything else has prior art to steal from.

## 11. Open Risks & Honest Notes

1. **Taint tracking is the linchpin — and it now has a research-backed design.** Implement the CaMeL/FIDES pattern rather than a homegrown scheme: a **privileged planner** that sees only trusted (blessed) human input and generates the execution plan; **quarantined subagents** that process untrusted content (web, files, messages) with no tool access — the §7 read-only web subagent *is* the quarantined LLM; and a **deterministic reference monitor** that tracks data provenance through every operation and enforces policy at tool-call boundaries. Injection becomes a data-flow violation ("is this data allowed there?"), not a text-pattern detection problem ("does this look malicious?"). If taint propagation is leaky (e.g., web content summarized into "clean" text), the defense is theater — provenance must survive transformation, which is exactly what the CaMeL-style value-wrapping addresses. Known cost: this pattern constrains open-ended dynamic tool calling; accept the utility tax for tainted sessions. See also OpenClaw's CaMeL RFC (session-level taint: once any taint-source tool runs, all subsequent side-effect arguments are tainted) as a free design document.
2. **Runtime enforcement must be real** — namespaces/containers/NetworkPolicy on hardware Josh controls, not prompt-level pleading. The cluster makes this enforceable; keep it that way.
3. **Parental visibility into kids' partitions** is a trust decision inside the family, not a technical one — but it has a technical consequence: the enforcement layer must distinguish *parent-of* relationships from *operator-of* privileges, or spousal privacy is only conventional. Revisit visibility scope as children age.
4. **"Background getting smarter" is unproven.** Autonomous writes remove the approval bottleneck, but memory *quality* (dedup, contradiction resolution, staleness) has no owner yet. Expect to need a compaction/consolidation behavior; ⏸ design later, but budget for it.
5. **Same-entity-across-modalities** implies shared session/state infrastructure on the cluster from day one — this is the actual hard engineering, more than any AI piece.
6. **Credential isolation is a hard requirement, not a nicety.** Secrets (API keys, OAuth tokens, service credentials) never enter any subagent's context or environment — display-layer redaction is not isolation (a documented failure mode in contemporary agents). The runtime brokers access: subagents receive scoped, revocable, short-lived tokens minted per-spawn, and the broker holds the real credentials outside every container boundary. Model: IronCurtain's credentials-never-enter-the-container OAuth handling; ZeroClaw's encrypted-secrets-and-allowlists-by-default posture.
7. ⏸ **When should the orchestrator initiate rather than respond?** The spec defines what happens when a user speaks, but not what licenses the entity to speak first — nor what it is allowed to observe in order to have something to say. Unaddressed in MVP; see the OpenAGI reference in §12 for one worked framing.

## 12. References

Credit where due — projects and formats that directly shaped this design:

- **IronCurtain** — Niels Provos. Policy-driven secure runtime for AI agents; source of the constitution-compiled policy model and the blessed-input trust mechanism (§6), and the honest security framing in §11. https://ironcurtain.dev/ · https://github.com/provos/ironcurtain
- **Hermes Agent** — Nous Research (MIT). One-agent/one-memory-across-surfaces architecture validating §2; surface gateway, subagent isolation, and sandbox-backend patterns. https://hermes-agent.nousresearch.com/ · https://github.com/NousResearch/hermes-agent
- **Open Knowledge Format (OKF)** — Google Cloud Platform. Memory storage format adopted in §4: markdown + YAML frontmatter bundles, human- and agent-readable, git-native. https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf
- **QMD** — local-first hybrid retrieval over markdown, adopted as the §4 retrieval mechanism.
- **Letta / MemGPT** — explicit, editable memory-block pattern that informed the inspectable file-based memory decision in §4.
- **Zep (Graphiti) & Mem0** — surveyed for §4; their temporal-graph and dual-store approaches define the upgrade path if file-based retrieval hits its ceiling.
- **CaMeL — "Defeating Prompt Injections by Design"** (Debenedetti et al., Google DeepMind / ETH Zürich). The injection-defense architecture adopted in §11.1: privileged planner / quarantined processor / capability-carrying values / deterministic policy enforcement at tool-call boundaries. https://arxiv.org/abs/2503.18813
- **"Adaptive Evaluation of Out-of-Band Defenses Against Prompt Injection in LLM Agents"** — survey organizing CaMeL, FIDES, Progent, RTBAS, FORGE, and Dual-LLM as classical reference-monitor / information-flow primitives; the map of the design space for §6/§7 enforcement. https://arxiv.org/abs/2606.26479
- **OpenClaw** — open-source self-hosted multi-channel assistant; its CaMeL RFC (issue #39160) specifies session-level taint tracking directly comparable to §7. https://github.com/openclaw/openclaw
- **ZeroClaw** — Rust-native agent framework; trait-based swappable providers/channels/memory, sandbox controls, encrypted secrets, allowlisted operations, and scoped filesystem access by default. Reference posture for §11.6.
- **Honcho** — dialectic user-modeling layer (used by Hermes's self-improving loop); candidate approach for the §11.4 memory-quality problem.
- **OpenAGI** (spshulem) — self-hosted proactive agent daemon. Not a substrate candidate: single-user, specialists run in-process sharing one privilege domain, tiered-JSONL memory rather than OKF, and its headline screen-observation input is host-level and does not survive containerization. Two ideas influenced this design anyway: *Directional Adaptive Scrutiny* — scoring an incoming signal across seven axes (urgency, impact, novelty, risk, confidence, specificity, conflict) and resolving to act / ask / watch / ignore / delegate — as a worked framing for §11.7; and its promote/demote **condenser** over tiered memory as a candidate mechanism for the §11.4 memory-quality problem. Licensed PolyForm Noncommercial 1.0.0, which constrains reuse of code as opposed to ideas. https://www.openagi.sh/ · https://github.com/spshulem/openAGI
