# Architecture Boundaries and Incremental Path

This is the working boundary for developing HearthAI incrementally. It deliberately does **not** choose a permanent memory backend or workflow engine.

## Portable memory boundary

```mermaid
flowchart TB
    CC["Claude Code<br/>host adapter"]
    OW["OpenWebUI<br/>host adapter"]

    API["OpenAPI contract<br/>save · recall · list · health"]
    CORE["HearthAI memory core<br/>memory semantics · identity · revocable host keys"]
    ADAPTER["Replaceable storage adapter"]

    FILES[("Files + Git<br/>starting implementation")]
    GRAPH[("Graph store<br/>only when observed queries justify it")]
    EVENTS["Memory events"]
    N8N["Workflow engine<br/>for example, n8n"]

    CC --> API
    OW --> API
    API --> CORE
    CORE --> ADAPTER
    ADAPTER --> FILES
    ADAPTER -. "possible later backend" .-> GRAPH
    CORE -.-> EVENTS
    EVENTS -. "possible later consumer" .-> N8N

    classDef host fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#1f2937
    classDef contract fill:#dbeafe,stroke:#2563eb,stroke-width:3px,color:#1f2937
    classDef core fill:#ccfbf1,stroke:#0f766e,stroke-width:3px,color:#1f2937
    classDef current fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#1f2937
    classDef deferred fill:#f8fafc,stroke:#94a3b8,stroke-width:2px,stroke-dasharray:5 5,color:#475569

    class CC,OW host
    class API contract
    class CORE,ADAPTER core
    class FILES current
    class GRAPH,EVENTS,N8N deferred
```

The OpenAPI schema is the portable contract. Hosts may register or invoke its operations differently, but host-specific mechanics do not enter HearthAI's memory semantics. The contract also does not expose file paths, Git commits, graph nodes, or workflow-engine concepts.

The first implementation may continue using files and Git behind the storage adapter. That is a cheap way to begin gathering evidence, not a permanent architecture decision. A graph store is justified only by observed queries or update patterns that the simpler backend cannot serve. A workflow engine is a separate concern: it may react to memory events or schedule work, but it is not the memory substrate.

## Incremental runway

```mermaid
flowchart LR
    P1["1 · Continuity<br/>Explicitly save in Claude Code<br/>Recall in OpenWebUI, and vice versa"]
    P2["2 · Observe<br/>Record friction, misses, latency,<br/>corrections, and actual queries"]
    D{"Evidence requires<br/>more structure or automation?"}
    P3A["3A · Keep it simple<br/>Improve the current adapter"]
    P3B["3B · Earn complexity<br/>Add graph retrieval, workflows,<br/>or another evidence-backed component"]

    P1 --> P2 --> D
    D -- "No" --> P3A
    D -- "Yes" --> P3B

    classDef phase fill:#ccfbf1,stroke:#0f766e,stroke-width:2px,color:#1f2937
    classDef decision fill:#fef3c7,stroke:#a16207,stroke-width:2px,color:#1f2937
    classDef outcome fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#1f2937

    class P1,P2 phase
    class D decision
    class P3A,P3B outcome
```

The first value proof is intentionally small:

1. One person uses Claude Code and OpenWebUI.
2. Memories enter HearthAI only through an explicit save instruction.
3. Each host has an independently revocable API key stored outside prompts.
4. A memory saved through either host is accessible through the other.

Cross-host continuity is sufficient to validate the first increment. Semantic retrieval, temporal reasoning, automatic extraction, household sharing, Neo4j, and n8n remain later hypotheses until usage produces evidence for them.
