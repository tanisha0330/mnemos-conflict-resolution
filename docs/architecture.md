# Architecture

```mermaid
flowchart TB
    subgraph Agents["Demo agents"]
        PA["payment-agent<br/>(mock Stripe API)"]
        SA["support-agent<br/>(mock Zendesk)"]
        FA["fulfillment-agent<br/>(reads memory)"]
    end

    subgraph AWS["AWS"]
        Bedrock["Amazon Bedrock<br/>Titan Embed V2 + Nova Lite / Claude Haiku"]
        Lambda["Lambda<br/>resolution worker (EventBridge-polled)"]
        Fargate["Fargate<br/>demo agent tasks"]
        S3["S3<br/>source artifacts"]
    end

    subgraph Pipeline["mnemos resolution pipeline (src/)"]
        Ingest["Ingestion<br/>claim extraction + embedding + subject_key"]
        Stage1["Stage 1: conflict detection<br/>cosine similarity vs canonical"]
        Stage2["Stage 2: deterministic rules<br/>authority tier -> recency -> confidence floor"]
        Arbiter["LLM arbiter (Bedrock)<br/>only when rules can't decide"]
        Commit["Transactional commit<br/>SERIALIZABLE + 40001 retry<br/>(never wraps the arbiter call)"]
        API["Retrieval API<br/>search / get_all / history / as_of"]
    end

    subgraph CRDB["CockroachDB"]
        Sources[("sources")]
        Subjects[("subjects")]
        Beliefs[("beliefs<br/>VECTOR(1024), vector_cosine_ops index")]
        Resolutions[("resolutions")]
    end

    PA -->|"raw text + source_id"| Ingest
    SA -->|"raw text + source_id"| Ingest
    Ingest -->|"embedding"| Bedrock
    Ingest --> Stage1
    Stage1 -->|"vector search <=>"| Beliefs
    Stage1 -->|"real conflict"| Stage2
    Stage1 -->|"no conflict / duplicate"| Beliefs
    Stage2 -->|"decided"| Commit
    Stage2 -->|"needs_llm"| Arbiter
    Arbiter -->|"Converse API, forced tool-use"| Bedrock
    Arbiter -->|"decision"| Commit
    Commit --> Subjects
    Commit --> Beliefs
    Commit --> Resolutions
    API --> Beliefs
    API --> Resolutions
    API --> Subjects
    FA -->|"search() / as_of()"| API

    S3 -.->|"provenance link via source_id"| Sources
    Lambda -.->|"IaC only, not deployed"| Commit
    Fargate -.->|"IaC only, not deployed"| Agents
```

## Notes

- **CockroachDB is the source of truth**, not a cache: claims are attributed, never overwritten, and the only place canonical state actually lives.
- **The arbiter call always happens before the commit transaction opens** — verified explicitly in `docs/REVIEW_LOG.md` (Block 2B checkpoint), not just asserted here.
- **Lambda and Fargate are IaC-only** (`infra/`, validated via `cdk synth`), not currently deployed — see `docs/REVIEW_LOG.md` (Block 4A) for why and how to deploy them yourself.
- Full block-by-block build history and every checkpoint's real findings: `docs/REVIEW_LOG.md`.
