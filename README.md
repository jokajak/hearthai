# hearthai

**An AI for a household, not for a person.** The household is the unit hearthai is designed
around — not one assistant with extra user accounts bolted onto it.

Everyone in the house gets **their own persona**: an assistant bound to their own private memory
space, which knows their history, their preferences, and their ongoing concerns, and which no
one else in the house can read. Alongside those sit **shared personas** — a home manager is the
obvious one — that every member can talk to, working from the household's shared memory rather
than from anyone's private space.

**Memory is what makes this worth more than a chatbot.** A model without context can answer
questions; it cannot notice that the dentist appointment collides with a soccer match, that this
is the third time this quarter the same bill has been queried, or that a decision made in March
is the reason for the constraint being hit in November. The context accumulated per person and
per household *is* the product. The other five components exist to gather that context safely,
keep it good, and make sure it never leaks across the people it belongs to.

It is one entity across surfaces — phone, web, terminal — and it has almost no privileges of its
own: everything it does to the world happens inside a constrained, ephemeral subagent that the
runtime, not the prompt, keeps on a leash.

**Status: pre-design.** Nothing is built yet. The behavior this is aiming at is specified in
[`docs/SPEC.md`](docs/SPEC.md); this README is the implementation-side view of that document —
the parts the system is made of. No technology choice below is committed; every one is a
candidate.

Licensed AGPL-3.0.

## Design stance

- **Memory is the product; the rest is plumbing.** Five components exist to let the sixth
  accumulate trustworthy context per person and per household. When a tradeoff is unclear,
  resolve it in favour of memory that is durable, legible, and correctly partitioned.
- **Six components, each with a contract.** Any one can be swapped without rewriting the others.
  If a feature does not fit a component's contract, that is a signal to widen the contract on
  purpose, not to grow a seventh box by accident.
- **Most features are prompts and policy, not components.** Personas, routing, tone, and the
  security constitution are all text over these six. That is where the leverage is — and why the
  component list can stay this short.
- **The orchestrator is deliberately low-privilege.** Its effective power is exactly the union of
  the subagent profiles it can spawn, so the spawn catalog is the security boundary: keep it
  small and reviewed (→ [SPEC §6](docs/SPEC.md#6-orchestrator--subagents--security-model)).

## The household model

### People and spaces

Every person in the house holds **one private memory space**. The household holds **one shared
space**. Spaces are fail-closed: a space an identity does not hold is not filtered out of results
at query time, it is unreachable. Group claims from the identity provider (`adults`, `kids`,
per-user) are what map a verified person onto the spaces they hold
(→ [SPEC §3](docs/SPEC.md#3-identity--access),
[§4](docs/SPEC.md#4-memory-architecture)).

```
     Josh              spouse              kid
       │                  │                  │            each addresses any persona:
       │                  │                  │            their own, or a shared one
       ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ session  │      │ session  │      │ session  │   a persona is the system prompt
  │ as Josh  │      │as spouse │      │  as kid  │   this session runs under
  └──┬────▲──┘      └──┬────▲──┘      └──┬────▲──┘
     │    │            │    │            │    │
   write read        write read        write read
     │    │            │    │            │    │
  ┌──▼────┴──┐      ┌──▼────┴──┐      ┌──▼────┴──┐
  │  Josh's  │      │ spouse's │      │   kid's  │   private stores — fail-closed
  │  store   │      │  store   │      │  store   │   between people, never within
  └──────────┘      └──────────┘      └──────────┘   a person
       ╎                  ╎                  ╎
       ╎ promotion        ╎ promotion        ╎ promotion    ← human-approved, always
       ╎ (approved)       ╎ (approved)       ╎ (approved)
       ▼                  ▼                  ▼
  ┌───────────────────────────────────────────────┐
  │              household store                  │  readable by every member
  │   no autonomous writer — promotion only       │  written only by approval
  └───────────────────────────────────────────────┘
              ▲          ▲          ▲
              └──────────┴──────────┘
                    read, by anyone who holds it
```

### A persona is a system prompt plus the stores you already hold

A persona is two things: the system prompt that gives it its direction, and the set of data
stores available to it for context. What separates a personal persona from a shared one is what
its prompt is *for*, not what it is allowed to see.

| | Its prompt directs it toward | Reads | Writes land in |
|---|---|---|---|
| **Personal persona** | that person's own life and concerns | their own store, plus the household store | their own store, tagged with the persona |
| **Shared persona** | household-level concerns | the same — whatever the addressing member holds | the addressing member's store, tagged with the persona |

Two rules make that table work:

- **Access is a property of the person, not the persona.** A session reads every store its member
  holds. A persona shapes *attention*, never *access*: it can reach nothing the member could not
  reach by addressing a different persona, so gating it would degrade answers without moving any
  boundary.
- **The only access boundary is between people.** It lives in the memory connector and keys off
  the verified identity — not in the prompt, and not between one person's own personas.

**Domain personas are a direction, not a partition.** The medical advisor is not Josh's persona
restricted to medical content; it is a prompt that orients the conversation toward medical
concerns while reading everything Josh holds. That is the point: it is what lets the medical
advisor notice that a financial pressure is driving a health decision, which a partitioned view
would make structurally impossible. (This revised
[SPEC §5](docs/SPEC.md#5-personas), which originally partitioned reads per persona; the section
now records the correction.)

### Writes, and what the tags are for

Reads are wide; writes are narrow and attributed.

- **Every write lands in the session member's own store**, autonomously
  (→ [SPEC §4.1](docs/SPEC.md#41-write-policy-reconciled) item 1), tagged with the persona that
  produced it.
- **The household store has no autonomous writer.** Nothing writes to it except by
  human-approved promotion (item 2) — not a personal persona, not a shared one, not the
  orchestrator. Talking to a household-facing persona changes the *tag* on the write, never its
  destination.

The tags then pay for themselves several times over, none of it access control:

- **Attribution** — the home manager believes X, the financial advisor believes Y, and you can
  tell which is which.
- **Promotion candidacy** — content written under a household-facing persona is the natural set
  to offer for promotion, so the gate has something specific to ask about.
- **Review and revert granularity** — git history that can be read one persona at a time.
- **Relevance** — a persona can surface its own prior context first without being blind to the
  rest of the store.

**Tags are provenance, not permissions.** The moment they are load-bearing for access, the real
boundary has been moved into a place that cannot enforce it.

### A persona is a prompt, not a process

There is no long-running home manager sitting in the house accumulating conversations. A session
belongs to **one member**; the persona is the prompt that session runs under; memory access
derives from that member's verified identity. A shared persona therefore reaches exactly what
the member addressing it reaches — no more, and no less. Talking to the home manager on Tuesday cannot surface what
someone else told the home manager on Monday, because nothing carried over: the persona is text,
and the only thing that persists is memory, which is scoped per identity.

One implementation consequence is worth writing down, since it is only true by construction:
**session state is keyed by the member, never by the persona.** Same prompt, different people,
no shared conversational memory between them.

One property still needs handling explicitly: **a shared persona serves every member but is
governed per speaker.** It needs to know it is talking to a kid rather than an adult, because a
kid's constitution is stricter
(→ [SPEC §6](docs/SPEC.md#6-orchestrator--subagents--security-model)). Same prompt, same stores
available, different rules per caller.

### Guardianship is not operation

Visibility follows the guardianship relationship, not who runs the cluster. Parents have insight
into their children's personas; **adults' spaces are private to each adult**, and operating the
infrastructure grants no read access to a spouse's space. The enforcement layer therefore has to
distinguish *parent-of* from *operator-of*, or spousal privacy is merely conventional. This is a
family-policy decision with a technical consequence, it should be disclosed to household members
rather than silent, and it narrows as children reach adulthood
(→ [SPEC §5](docs/SPEC.md#5-personas),
[§11.3](docs/SPEC.md#11-open-risks--honest-notes)).

## Architecture at a glance

```
        family members (OIDC-verified)
                    │
        ┌───────────▼───────────┐
        │    user interface     │  web/phone chat · terminal session
        │  (identity + surface) │  hands over a verified claim, never chat-asserted
        └───────────┬───────────┘
                    │ identity, space, modality
        ┌───────────▼───────────┐        ┌──────────────────┐
        │     orchestrator      │◄──────►│ inference engine │  pluggable models
        │  (low privilege: no   │        └──────────────────┘  cloud-primary + laptop
        │   direct tools, no    │
        │   direct net, no      │        ┌──────────────────┐
        │   direct secrets)     │◄──────►│ memory manager   │  what to keep, dedup,
        └───────────┬───────────┘        │                  │  staleness, persona views
                    │                    └────────┬─────────┘
                    │ spawn request               │
        ┌───────────▼───────────┐        ┌────────▼─────────┐
        │    agent isolation    │        │ memory connector │  OKF bundles, git,
        │  profiles · blessed/  │        │  (fail-closed    │  retrieval
        │  unblessed provenance │        │   by space)      │
        │  reference monitor    │        └──────────────────┘
        └───────────┬───────────┘
                    │ approved tool call
        ┌───────────▼───────────┐
        │ tool execution        │  sandbox · network policy · credential broker
        │ isolation             │  projects results before they reach any model
        └───────────────────────┘
```

Read it as two axes: **memory connector/manager** decide what the entity knows, and
**agent/tool isolation** decide what it may do. The orchestrator sits between them holding
neither capability directly.

## Components

Each component is described the same way: what it is responsible for, its contract, the
candidate technology (provisional), the spec sections it satisfies, and what is explicitly not
its job.

### 1. Inference engine

**Responsibility.** Model access behind one interface, so that which model answers is a routing
decision rather than an architectural one.

**Contract.** In: messages, tool schemas, and a routing hint. Out: a completion. Guarantees: the
caller never encodes a provider; routing is data the caller supplies, not logic the caller
implements. Today the hint is availability-shaped; the interface must be able to carry a
*sensitivity* hint later without a caller rewrite.

**Candidate (provisional).** LiteLLM as the pluggable layer. Cloud APIs as the primary brain,
with the laptop as an opportunistic local endpoint when it happens to be on.

**Satisfies.** [SPEC §8](docs/SPEC.md#8-inference-tiering).

**Not its job.** Deciding whether a given conversation is *allowed* to leave the house — that is
policy, and it enters as a routing hint. The engine enforces nothing.

### 2. User interface

**Responsibility.** The surfaces the entity is reachable through, and the point at which a human
becomes a verified identity.

**Contract.** In: an authenticated human on some surface. Out: a session carrying a **verified
identity claim**, the memory spaces that identity holds, and modality metadata. Guarantees:
identity is never a fact asserted in chat text; a surface that cannot verify identity cannot
open a session. The terminal surface additionally registers the laptop as an execution endpoint
for the lifetime of that session — presence is the approval, and the capability dies with the
session.

**Candidate (provisional).** Authentik OIDC in front of everything, with group claims
(`adults`, `kids`, per-user) mapped to memory spaces. Web/phone chat first; terminal second.

**Satisfies.** [SPEC §2](docs/SPEC.md#2-modalities),
[§3](docs/SPEC.md#3-identity--access),
[§7](docs/SPEC.md#7-trust-flow--trust-flows-down-never-up) (item 3).

**Not its job.** Deciding what a given identity may read or write. It states who this is; the
memory connector and agent isolation act on that.

### 3. Tool execution isolation

**Responsibility.** Where tool calls actually run, what they may touch, and what comes back.

**Contract.** In: a tool call plus the scoped profile it runs under. Out: a *projected* result.
Guarantees: (a) filesystem, network, and process constraints are enforced by the runtime, not
requested of the model; (b) raw tool payloads never enter inference context — every result is
projected to task-relevant fields before injection, with quarantined summarization only as a
last resort and always provenance-labelled; (c) secrets never enter a subagent's context or
environment — the credential broker holds the real credentials outside every container boundary
and mints short-lived scoped tokens per spawn.

**Candidate (provisional).** Containers plus NetworkPolicy on the home Kubernetes cluster; MCP
retained as the standardization layer for system access; IronCurtain evaluated as the
enforcement runtime under our own orchestrator rather than as a substrate.

**Satisfies.** [SPEC §6](docs/SPEC.md#6-orchestrator--subagents--security-model),
[§11.2](docs/SPEC.md#11-open-risks--honest-notes),
[§11.6](docs/SPEC.md#11-open-risks--honest-notes).

**Not its job.** Deciding whether a call is permitted. It is the jail, not the judge; agent
isolation decides, this executes what was allowed.

### 4. Agent isolation

**Responsibility.** Who may spawn what, and how data provenance survives the trip between
agents. This is the injection defense.

**Contract.** In: a spawn request plus the current session's provenance state. Out: an ephemeral
subagent bound to a catalog profile, or a denial, or an escalation to a human. Guarantees:
direct human input is **blessed**; web, file, and message content is **unblessed** by default;
provenance is tracked through every operation and survives transformation (including
summarization); policy is evaluated deterministically at tool-call boundaries. Injection becomes
a data-flow question — *is this data allowed here?* — not a text-pattern question.

Shape: a **privileged planner** that sees only blessed input and produces the plan;
**quarantined processors** that read untrusted content with no tool access; a **deterministic
reference monitor** that decides. A session that has ingested unblessed content cannot write
memory or spawn write-capable subagents without a human.

**Candidate (provisional).** A plain-English constitution compiled into allow/escalate/deny
rules, with per-space constitutions (a kid's is stricter than an adult's). CaMeL/FIDES as the
reference architecture.

**Satisfies.** [SPEC §6](docs/SPEC.md#6-orchestrator--subagents--security-model),
[§7](docs/SPEC.md#7-trust-flow--trust-flows-down-never-up),
[§11.1](docs/SPEC.md#11-open-risks--honest-notes).

**Not its job.** Sandboxing. It says yes, no, or ask-a-human; tool execution isolation makes the
yes safe to run.

### 5. Memory connector

**Responsibility.** Memory as *storage*: getting bytes in and out of the right space, and
nowhere else.

**Contract.** In: an identity, a space, and a read or write. Out: bundle content, or a commit.
Guarantees: **fail-closed** — a space the identity does not hold is not filtered at query time,
it is unreachable; every write is a git commit with a `log.md` entry, so review and revert are
post-hoc rather than pre-approval; writes land in the session member's own store, autonomously,
carrying the tag of the persona that produced them; the household store has no autonomous writer
at all, and cross-space writes are not this component's to make. Tags are recorded as provenance
and are never consulted for access — the identity boundary between people is the only gate.

**Candidate (provisional).** OKF bundles (markdown + YAML frontmatter, one per space), git for
versioning and audit, QMD for retrieval. No vector or graph database until a concrete query
fails without one.

**Satisfies.** [SPEC §4](docs/SPEC.md#4-memory-architecture),
[§4.1](docs/SPEC.md#41-write-policy-reconciled).

**Not its job.** Judging whether something is worth remembering, or whether two memories
contradict. It stores what it is told, in the space it is allowed to.

### 6. Memory manager

**Responsibility.** Memory as *a thing that has to stay good over years*. Also the owner of
persona scoping.

**Contract.** In: conversation, retrieval results, and the existing bundles. Out: write
proposals, consolidations, and scoped memory views. Guarantees: it may **propose** a cross-space
promotion (personal → household) and never perform one — promotion is human-approved, always. It
owns dedup, contradiction resolution, and staleness. It constructs both persona kinds from one
rule — **a system prompt plus the stores the member already holds** — where personal, shared, and
domain personas differ in what their prompt is for, not in what they may read. It owns the
per-persona write tagging that makes attribution and promotion candidacy work. A persona is never
a separate agent with private memory of its own, and holds no conversational state across the
members who address it.

**Candidate (provisional).** Frontmatter-`type` view construction over the connector's bundles;
a promote/demote condenser for consolidation; Honcho-style user modeling as an option for
quality.

**Satisfies.** [SPEC §4.1](docs/SPEC.md#41-write-policy-reconciled),
[§5](docs/SPEC.md#5-personas),
[§11.4](docs/SPEC.md#11-open-risks--honest-notes).

**Not its job.** Enforcing who may see a store — that is the connector's fail-closed boundary
between people. A persona directs attention within what the member already holds; it never
extends access, and its tags are provenance rather than permissions.

## Isolation granularity

The unit of isolation is itself a design choice. There are two, and the difference between them
is whether a model runs inside the boundary.

**Isolated tool invocation** — no model inside. A sandbox starts, runs one call, returns a
projected result, and dies. Deterministic, cheap enough to use per call, and the provenance
label attaches to the returned value at the boundary.

**Isolated sub-agent** — a model inside. It reasons over content and may iterate. Needed only
when untrusted content must be *interpreted* to produce the answer: "which of these three pages
answers my question" cannot be a deterministic projection.

The rule that follows: **deterministic projection → isolated tool invocation; interpretation
required → isolated sub-agent with no tool access.** This is the same ladder as the projection
tiers in [SPEC §6](docs/SPEC.md#6-orchestrator--subagents--security-model), seen from the
isolation side rather than the context-economy side — one boundary, two motivations.

### Sub-agent boundaries are taint firebreaks

Session-level taint is a ratchet. Read one web page in the main session and, per
[SPEC §7](docs/SPEC.md#7-trust-flow--trust-flows-down-never-up), nothing in that session may
write memory again. For an assistant that reads the web constantly, that is a severe utility
tax paid on the first fetch of the day.

Run the read inside a sub-agent instead and the *sub-agent's* session takes the taint. The
parent receives a labeled value rather than a poisoned session — value-level precision using
only session-level machinery, with no CaMeL interpreter required.

This holds only while the return channel stays narrow and the parent treats the result as an
opaque value. A sub-agent that returns free text which the parent's model then reads has moved
the taint, not bounded it: the parent is injection-influenced whatever the label says. So the
firebreak reduces the ratchet from session-wide and permanent to per-value and trackable — a
large win, but not a substitute for value discipline
(→ [SPEC §11.1](docs/SPEC.md#11-open-risks--honest-notes)).

### One profile, three instantiations

Both granularities are the same machinery with different fields set:

| Profile | Model inside? | Tools | Sees | Returns |
|---|---|---|---|---|
| Tool-runner | no | one, fixed | its own arguments | projected value |
| Quarantined processor | yes | none | unblessed content | labeled value |
| Privileged planner | yes | many, via catalog | blessed input only | a plan |

The runtime, the spawn path, and the reference monitor are shared; a profile is data, not code.
This is why supporting both granularities costs roughly one system rather than two — and why the
catalog stays small enough to actually review.

## What is a prompt, not a component

The point of keeping the component list at six is that this list can grow freely. Adding a person
to the household, or a new shared persona, is configuration and prose — not a new subsystem:

| Behavior | Lives as | Enforced by |
|---|---|---|
| A new household member's persona | Prompt; access follows their verified identity | Memory connector's fail-closed boundary between people; group claims decide what they hold |
| A new shared persona (home manager, meal planner, trip planner) | Prompt directed at household concerns | Same boundary — it runs as the member addressing it, so it can reach nothing they cannot |
| Domain advisors (medical, financial, life) | Prompt giving the conversation a direction | Nothing needs to — they read what the member holds; the identity boundary is the only gate |
| The security constitution | Plain English, compiled | Agent isolation's reference monitor, at tool-call boundaries |
| "This looks worth promoting to the household space" | Prompt-driven suggestion | Memory manager proposes; a human approves; the connector writes |
| Tone, voice, house style | Prompt | Nothing — and nothing needs to |
| "Don't do anything risky" | **Not a prompt.** | Agent isolation + tool execution isolation. Prompts are never the enforcement point. |

## Invariants

These hold regardless of how any component is implemented. A change that breaks one is a change
to the architecture, not to a component.

1. **Identity is a verified claim, never text.** No path exists by which chat content sets who
   the user is.
2. **Memory spaces fail closed.** Unreachable, not filtered.
3. **Unblessed data cannot reach a memory write or an external action without a human.**
4. **The household store has no autonomous writer.** Every write lands in a person's own store;
   the only path into shared memory is human-approved promotion. The entity may suggest; it may
   not promote.
5. **Secrets never enter a subagent's context or environment.** Redaction at the display layer
   is not isolation.
6. **Raw tool payloads never enter inference context.** Project before inject.
7. **Enforcement is runtime, never prompt-level pleading.** Namespaces, containers, and network
   policy on hardware we control.
8. **Session state is keyed by the member, never by the persona.** A persona is a prompt applied
   to one member's session; access derives from that member's identity, and no persona extends
   or restricts it.

During phase 1 two of these are temporarily bent by scaffolding, with stated exit conditions —
see [Build order](#scaffolding-with-exit-conditions). No others are negotiable.

## MVP scope

Per component — condensed from [SPEC §9](docs/SPEC.md#9-mvp-scope).

| Component | In MVP | Deferred |
|---|---|---|
| Inference engine | Pluggable model access, cloud-primary; laptop endpoint when present | Sensitivity-based tiering (must stay expressible) |
| User interface | Cluster-hosted web chat behind Authentik OIDC; terminal-as-execution-endpoint session model | Additional surfaces; household onboarding UX |
| Tool execution isolation | Sandboxed tool-runners, result projection, credential broker | External-write allowlists (every external write is human-approved in MVP) |
| Agent isolation | Predefined profile catalog, read-only web subagent, blessed/unblessed taint tracking | Orchestrator-proposed new profiles |
| Memory connector | Two OKF bundles — one personal space plus the household shared space — QMD retrieval, git versioning | Spouse, kid-safe, and per-kid spaces; vector/graph retrieval |
| Memory manager | One personal persona set plus domain personas, one shared persona, per-persona write tagging, autonomous writes to the member's own store, human-gated promotion | Additional people's personas; consolidation/compaction; promotion-friction UX |

## Build order

**The memory layer is built first and standalone**, as a plugin consumed by an existing agent,
while the other five components are built the way we want them. The orchestrator is the last
thing to arrive, not the first.

Why this order:

- **Memory has the longest feedback loop.** Whether consolidation, dedup, and staleness handling
  actually work (→ [SPEC §11.4](docs/SPEC.md#11-open-risks--honest-notes)) cannot be learned
  from a week of synthetic testing; it takes real writes over real months. Building it first
  starts that clock immediately.
- **It is the differentiated part.** Orchestrators are commodity — Hermes, OpenClaw and others
  all have one worth borrowing. Household-multi-tenant, human-legible, git-versioned memory is
  the thing that does not exist yet.
- **It is the least coupled component.** The connector and manager need an identity and a space.
  They do not need the isolation runtime to exist.
- **A plugin boundary proves the contract.** Exposed as a serializable interface to a foreign
  host, the connector cannot quietly grow in-process coupling to our own orchestrator. The
  six-replaceable-components stance stops being an assertion and becomes a tested fact.

### What the interface carries from day one

These are arguments on the API from the first commit, even where the interim host cannot supply
them meaningfully:

- **Identity and space** — parameters, never configuration, even while there is exactly one of
  each.
- **Provenance on every write** — blessed or unblessed, supplied by the caller.
- **Promotion as an operation distinct from a write** — one that returns *proposed*, never
  *done*.

An interface that gains these later has to gain them in every caller at once. An interface born
with them just accumulates callers that fill them in properly.

### Scaffolding, with exit conditions

Phase 1 runs inside a foreign host that has no OIDC and no concept of taint. Two invariants are
bent, deliberately and temporarily:

| Invariant bent | Interim posture | Exit condition |
|---|---|---|
| #1 — identity is a verified claim | A single configured identity, asserted by the host | Our own user interface, with Authentik in front |
| #3 — unblessed data cannot reach a write without a human | The host supplies `blessed` for all writes | Our own agent isolation, with real provenance tracking |

The interim posture on #3 is narrowly legitimate rather than a straight cheat:
[SPEC §7](docs/SPEC.md#7-trust-flow--trust-flows-down-never-up) already holds that terminal
presence is the approval, so a terminal-hosted session driven by one adult *is* blessed input by
the spec's own rule. That justification does not extend to phone chat, to unattended runs, or to
a second person — which is precisely when the exit condition binds.

⚠ The failure mode to watch is scaffolding that works becoming the design. These exceptions are
only safe for as long as they stay written down next to the invariants they bend.

## Open questions

Each of these is unresolved and known — detail in
[SPEC §11](docs/SPEC.md#11-open-risks--honest-notes).

- **Taint propagation is the linchpin.** If provenance does not survive summarization, the whole
  defense is theater. Accepted cost: constrained dynamic tool calling in tainted sessions.
- **Memory quality has no owner yet.** Autonomous writes remove the bottleneck but not the
  entropy; the memory manager's consolidation behavior is unproven and unbuilt.
- **Same-entity-across-modalities is the real engineering.** Shared session and state
  infrastructure from day one, more than any model-side work.
- **Parent-of must be distinguishable from operator-of** in the enforcement layer, or spousal
  privacy is only conventional. Guardian visibility is a family-policy decision with a technical
  consequence, and it should sunset as children age.
- **What licenses the entity to speak first?** Proactive initiation — and what it may observe in
  order to have something to say — is unaddressed in MVP.

## Prior art

Steal from, do not adopt as substrate. None of these provides the load-bearing requirement:
identity-mapped household multi-tenancy. Full detail in
[SPEC §10](docs/SPEC.md#10-prior-art--positioning) and
[§12](docs/SPEC.md#12-references).

| Source | Contributes | To |
|---|---|---|
| [IronCurtain](https://github.com/provos/ironcurtain) | Constitution-compiled policy; blessed-input trust; credentials never enter the container | Agent isolation, tool execution isolation |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | One agent / one memory across surfaces; surface gateway; SSH sandbox backend | User interface |
| [CaMeL](https://arxiv.org/abs/2503.18813) | Privileged planner / quarantined processor / capability-carrying values / deterministic enforcement | Agent isolation |
| [OpenClaw](https://github.com/openclaw/openclaw) | Session-level taint tracking, specified as an RFC | Agent isolation |
| ZeroClaw | Encrypted secrets, allowlists and scoped filesystem by default; swappable providers | Tool execution isolation, inference engine |
| [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) | Markdown + YAML frontmatter bundles, git-native and `cat`-able | Memory connector |
| QMD | Local-first hybrid retrieval over markdown | Memory connector |
| Letta / MemGPT | Explicit, editable memory blocks — the inspectability argument | Memory connector |
| Zep (Graphiti), Mem0 | Temporal-graph and dual-store approaches — the upgrade path if files hit their ceiling | Memory connector |
| Honcho | Dialectic user modeling | Memory manager |
| OpenAGI | Directional Adaptive Scrutiny (act/ask/watch/ignore/delegate); promote/demote condenser | Memory manager, proactivity question |
