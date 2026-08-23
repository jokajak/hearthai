# hearthai

An AI that serves a group of people rather than one person. Everyone who uses it has a private
memory that nobody else can reach — not other members, not whoever runs the server — and shares
what they choose to by putting it in a datastore they invite others into.

**Memory is what makes it worth more than a chatbot.** A model without context can answer
questions; it cannot notice that the dentist appointment collides with a soccer match, that this
is the third time this quarter the same bill has come up, or that a decision made in March is the
reason for the constraint being hit in November. The accumulated context is the product.
Everything else exists to gather it, keep it good, and keep it where it belongs.

**Status: pre-design.** Nothing is built. [`docs/SPEC.md`](docs/SPEC.md) holds the longer
behavioral spec this is drawn from; where the two disagree, this README is current. No technology
choice here is committed. Licensed AGPL-3.0.

## How it works

```mermaid
flowchart TB
    U["Alice · Bob · Carol"]:::person
    OIDC["<b>OIDC login</b><br/>any provider"]:::gate
    H["<b>hearthai</b><br/>persona = a system prompt<br/>access follows the user"]:::core
    INF["<b>Inference</b><br/>pluggable via LiteLLM"]:::inf
    PRIV[("<b>Alice's private store</b><br/><i>never shareable</i>")]:::priv
    SH1[("<b>Family</b><br/>a datastore Alice<br/>was invited into")]:::shared
    SH2[("<b>Trip planning</b><br/>another one")]:::shared
    OK{{"Alice approves"}}:::gate

    U --> OIDC
    OIDC -->|"verified identity"| H
    H <--> INF
    H <-->|"read · write, autonomous"| PRIV
    SH1 -->|read| H
    SH2 -->|read| H
    H -. "proposed" .-> OK
    OK -. "approved — the only way in" .-> SH1
    OK -. "approved" .-> SH2

    classDef person fill:#e8ddf5,stroke:#6b46a8,stroke-width:2px,color:#1a1a1a
    classDef gate fill:#fde9c8,stroke:#c47f17,stroke-width:2px,color:#1a1a1a
    classDef core fill:#fff3bf,stroke:#a68b00,stroke-width:3px,color:#1a1a1a
    classDef inf fill:#e5e5e5,stroke:#666666,stroke-width:2px,color:#1a1a1a
    classDef priv fill:#c9e8d4,stroke:#1f6b41,stroke-width:2px,color:#1a1a1a
    classDef shared fill:#d6e9fb,stroke:#2a6fb0,stroke-width:2px,color:#1a1a1a
```

Alice's view. Bob and Carol each have the same shape: their own private store, plus whichever
datastores they have been invited into. Reads are wide — everything Alice holds. Writes are
narrow: straight into her private store, and into a shared datastore only when she says yes.

## Users and datastores

| Concept | Rule |
|---|---|
| **User** | Authenticates through any OIDC provider. Identity is a verified claim, never something asserted in chat. |
| **Private datastore** | Exactly one per user, created with the account. **Never shareable** — there is no invite, no admin override, no operator read. |
| **Shared datastore** | Created by a user, who invites others. Membership is the access rule; there is nothing else to configure. |
| **Access** | A session reads the user's private store plus every datastore they are a member of. Nothing else exists to it. |
| **Writes** | Autonomous into the user's own private store. Human-approved into any shared datastore. |

Three things follow that are worth being explicit about.

**A household is not a concept in the system.** It is a datastore someone created and invited
their family into. So are a couple's shared finances, a project with a colleague, and a trip with
friends. One mechanism covers all of them, and none of them needed to be designed for.

**Non-shareable means non-shareable.** The private store's unshareability is a property of the
store, not a permission that could be granted later. Running the server does not grant it. This
is the one thing in the system that has no override, which is what makes the rest safe to use
casually.

**Approval is what makes sharing deliberate.** Anything landing where other people can read it
passes a human first. The agent proposes; a person says yes. In practice this is conversational
— *"want me to put that in the family store?"* — not an administrative queue.

## Personas

A persona is **a system prompt**. It gives the conversation a direction — a home manager, a
medical advisor, a financial advisor — and that is the whole of it.

Access always follows the user, never the persona. A persona reads exactly what its user holds,
so switching personas cannot reach anything new, and restricting one would only make its answers
worse: a medical advisor that cannot see financial context cannot notice that a financial
pressure is driving a health decision. **Personas direct attention; the datastore membership
boundary is what actually gates.**

Adding a persona is therefore writing a prompt, not building a subsystem. So is adding a person,
or a new shared datastore. That is where most of hearthai's behavior is meant to live.

Writes carry the persona that produced them as a tag. That is **provenance, not permission** —
it supports attribution, promotion candidates, and per-persona review of git history, and it is
never consulted for access.

## Components

Six replaceable parts, each with a contract, so any one can be swapped. The first three are the
product; the last three are internal features that keep it honest.

| Component | Responsibility | Candidate (provisional) |
|---|---|---|
| **User interface** | Surfaces — web, phone, terminal — and the point where a person becomes a verified identity. Hands the session an identity claim and the datastores it holds. | OIDC against any provider |
| **Memory connector** | Read and write access to datastores. Fail-closed on membership: a store you are not in is unreachable, not filtered. Every write is a git commit. | OKF bundles (markdown + YAML frontmatter), git, QMD retrieval |
| **Memory manager** | Keeps memory good over years: what to keep, dedup, contradictions, staleness. Proposes writes into shared datastores; never performs them. Owns persona tagging. | Condenser over the bundles |
| **Inference engine** | Model access behind one interface, so which model answers is a routing decision. | LiteLLM; cloud primary, local when available |
| **Tool execution isolation** | Where tool calls run and what they may touch. Guarantees: sandboxed filesystem and network, secrets never in an agent's context or environment, results projected before they reach a model. | Containers + NetworkPolicy; MCP as the tool standard |
| **Agent isolation** | Which subagents may be spawned, and tracking whether data came from a person or from the web. Untrusted content cannot reach a write or an external action without a human. | Compiled policy; CaMeL-style planner/quarantine split |

The last two are the reason it is safe to let this thing read the web and touch real services.
They are deliberately absent from the diagram: they shape what happens inside a request, not what
hearthai *is*.

## Invariants

1. **A private datastore is never shareable.** No invitation, no admin, no operator.
2. **Identity is a verified claim, never text.** Nothing said in chat can change who you are.
3. **Datastore access fails closed.** Unreachable, not filtered.
4. **Writes into a shared datastore are always human-approved.** The agent proposes; a person
   decides.
5. **A persona never changes access.** It directs attention within what the user already holds.
6. **Secrets never enter an agent's context or environment.** Redaction at the display layer is
   not isolation.
7. **Enforcement is runtime, not prompt-level pleading.**

## Status and build order

MVP: OIDC login, one private datastore per user, user-created shared datastores with invitations,
OKF bundles under git with QMD retrieval, autonomous private writes with approved shared writes,
persona prompts, and pluggable inference. Deferred: consolidation and compaction behavior,
sensitivity-based inference routing, vector or graph retrieval, and any allowlist for external
writes — those stay human-approved.

**The memory layer gets built first**, standalone, as a plugin for an agent that already exists.
It has the longest feedback loop — whether dedup and staleness handling actually work takes months
of real writes to learn, not a week of synthetic testing — and it is the differentiated part,
since orchestrators are commodity and human-legible multi-user memory is not. Exposing it as a
serializable interface to a foreign host also proves the component contract instead of asserting
it. Identity, datastore, and write-provenance are parameters on that interface from the first
commit, even while the interim host can only supply one of each.

Two things are unresolved. **Memory quality has no owner yet** — autonomous writes remove the
bottleneck but not the entropy. And **nothing licenses the agent to speak first**; proactivity,
and what it may observe in order to have something to say, is unaddressed.

## Prior art

Detail and links in [`docs/SPEC.md`](docs/SPEC.md#12-references).

- **[IronCurtain](https://github.com/provos/ironcurtain)** — constitution-compiled policy;
  credentials that never enter the container.
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — one agent and one memory
  across many surfaces.
- **[CaMeL](https://arxiv.org/abs/2503.18813)** — privileged planner, quarantined processor,
  deterministic enforcement at tool-call boundaries.
- **[OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)** — markdown +
  YAML frontmatter bundles, git-native and readable with `cat`.
- **QMD** — local-first hybrid retrieval over markdown.
- **Letta / MemGPT** — explicit, editable memory blocks; the inspectability argument.
- **Zep (Graphiti), Mem0** — the upgrade path if file-based retrieval hits its ceiling.
- **ZeroClaw** — encrypted secrets and scoped filesystem access by default.
- **Honcho**, **OpenAGI** — candidate mechanisms for the memory-quality problem.

None of them provide the load-bearing requirement: per-user private memory with user-created
sharing on top.
