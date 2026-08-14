# mnemos

**Agentic memory that treats what agents observe as claims, not facts — and resolves the contradictions instead of quietly stacking them up.**

Most agentic memory systems are append-only logs with a search index bolted on. When two agents disagree about the same fact — a payment API says a refund went through, a support ticket says it's still pending — that disagreement just sits there as two rows, and whichever one a retrieval call happens to surface first becomes the agent's "truth" for that moment. mnemos does something different: every write is attributed to a source with a known authority level, conflicting claims are actively detected via vector similarity, and a two-stage resolution pipeline — cheap deterministic rules first, an LLM arbiter only when the rules genuinely can't decide — settles which claim is canonical, with the reasoning kept forever, not discarded. CockroachDB's serializable transactions make that resolution step correct even when dozens of agents are racing to write at once; a naive read-then-write implementation loses updates silently under the exact same load (measured, not assumed — see below).

## How this compares

|  | **Zep** | **Mem0** | **mnemos** |
|---|---|---|---|
| When claims conflict | Newer supersedes older by timestamp | Both are stored; retrieval doesn't reconcile them | Detected, then actively resolved — by rule first, LLM arbiter only if needed |
| Why one claim wins | Recency alone | It doesn't decide — that's left to whoever reads the memory later | Source authority, recency (on volatile subjects), and confidence, in that order — or an LLM's reasoned judgment when none of those apply |
| What happens to the losing claim | Generally overwritten or aged out | Kept, but retrieval has no way to know it lost | Never deleted — marked `superseded`, with the full resolution reasoning attached, queryable via `history()` and `as_of()` |
| Concurrency guarantee | Not the focus | Not the focus | Serializable transactions with explicit retry — proven under 200 concurrent writers with zero lost updates against the live cluster (not a claim, a measurement) |
| Default retrieval | Returns what's stored | Returns everything, conflicts and all | Returns only the resolved, canonical answer — `include_superseded=True` to see the rest |

The honest version of this pitch, not the marketing one: mnemos isn't claiming to out-scale Zep or out-integrate Mem0. The bet is narrower — that *contradiction is a first-class event a memory system should actively resolve*, not a retrieval-time ambiguity the caller has to sort out. Full build history, including everything that didn't work on the first try, is in [`docs/REVIEW_LOG.md`](docs/REVIEW_LOG.md) — including a real reliability finding about how reliably the system actually *detects* certain conflicts, which is exactly the kind of thing a positioning table like this one would otherwise paper over.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full Mermaid diagram: ingestion → Stage 1 similarity detection → Stage 2 deterministic rules → Bedrock LLM arbiter (only when needed) → transactional commit → retrieval API, with every CockroachDB and AWS component labeled.

## Setup

1. Clone the repo and move into it.

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   # macOS/Linux
   source .venv/bin/activate
   # Windows (Git Bash)
   source .venv/Scripts/activate
   # Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and fill in your own values:

   ```bash
   cp .env.example .env
   ```

   | Variable | Description |
   | --- | --- |
   | `DATABASE_URL` | CockroachDB connection string (CockroachDB Cloud console -> Connect) |
   | `AWS_REGION` | AWS region with Bedrock enabled, e.g. `us-east-1` |
   | `BEDROCK_EMBEDDING_MODEL_ID` | Bedrock model ID used for embeddings, e.g. `amazon.titan-embed-text-v2:0` |
   | `BEDROCK_ARBITER_MODEL_ID` | Bedrock model ID used as the conflict-resolution arbiter |

   `.env` is gitignored and never committed.

5. Verify your setup connects to CockroachDB and AWS correctly:

   ```bash
   python scripts/verify_setup.py
   ```

   This checks CockroachDB connectivity, AWS credentials (via STS), and Bedrock model access, printing ✅/❌ for each with a fix hint on failure.

6. Apply the schema and seed sample data:

   ```bash
   python -m src.schema.migrate
   python -m src.schema.seed
   ```

7. Try the demo CLI or the full Streamlit app:

   ```bash
   python -m scripts.demo_cli
   # or
   streamlit run src/demo/app.py
   ```

## API usage

```python
from src.api.client import MnemosClient

client = MnemosClient()  # uses DATABASE_URL from .env

# add() runs the full pipeline: claim extraction -> embedding -> subject_key
# assignment -> conflict detection -> rules/arbiter -> transactional commit
client.add(
    "Stripe: charge ch_1abc for order-12345 was refunded $49.99",
    agent_id="payment-agent",
    source_id=stripe_source_id,
)

# search() and get_all() return ONLY canonical beliefs by default - the
# resolved, current answer, not every conflicting claim ever made
client.search("refund status for order-12345", subject_key="refund_status:order-12345")
# -> [Belief(status='canonical', claim_text='Refund for order-12345 has been processed...', ...)]

# pass include_superseded=True to see the full picture, including what lost
client.search("refund status for order-12345", subject_key="refund_status:order-12345", include_superseded=True)
# -> also includes the superseded zendesk_tickets claim that was overruled

client.get_all("refund_status:order-12345")               # canonical only
client.get_all("refund_status:order-12345", include_superseded=True)  # everything

# history() returns every belief for a subject with the resolution reasoning
# that decided its fate, if any - the audit trail
for entry in client.history("refund_status:order-12345"):
    print(entry.belief.status, entry.belief.claim_text)
    if entry.resolution:
        print("  resolved because:", entry.resolution.reasoning)

# as_of() answers "what did we believe at time T", via CockroachDB's
# AS OF SYSTEM TIME - true time-travel, not a snapshot table
from datetime import datetime, timezone
result = client.as_of("refund_status:order-12345", datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc))
print(result.pretty())
# Subject: refund_status:order-12345
# As of:   2026-08-13T15:00:00+00:00
# Canonical: "Refund for order-12345 has been processed and completed" (source=stripe_api, confidence=0.98)
# Other claims at this time:
#   - [superseded] "Refund for order-12345 is still pending..." (source=zendesk_tickets) - new source authority_tier=5 outranks existing source authority_tier=3
```

## Concurrency: measured, not claimed

Same read-then-write pattern, run against both databases with **zero retry logic** — the honest baseline of what an unaware team would ship:

| | 50 concurrent writers | 200 concurrent writers |
|---|---|---|
| PostgreSQL (default `READ COMMITTED`) | 50/50 "succeeded", **41 silently lost** | 200/200 "succeeded", **163 silently lost** |
| CockroachDB (default `SERIALIZABLE`, no retry) | only 2/50 committed, rest errored loudly, **0 lost** | only 2/200 committed, rest errored loudly, **0 lost** |

The second row is the honest reason this project bothers with retry logic at all: CockroachDB never *corrupts* data under contention, but a client with no retry logic barely gets anything done. With the retry logic this project actually ships (`src/resolution/commit.py`, explicit `SQLSTATE 40001` handling with backoff), the same 50 and 200-writer loads resolve **every writer to a definitive, correct outcome — 0 lost updates, both scales**, against the live cluster. Full methodology and a chart: [`docs/concurrency-comparison.md`](docs/concurrency-comparison.md).

## Failure resilience

Node failure without memory downtime, watched live and reproduced twice against a genuine local 3-node CockroachDB cluster (this project's real cluster is Cloud Serverless, which doesn't expose node-level failure testing — see [`docs/failure-resilience.md`](docs/failure-resilience.md) for why, and the full transcripts of both runs).

## Built with

**CockroachDB:**
- **Distributed Vector Indexing** — `beliefs.embedding VECTOR(1024)` with a `vector_cosine_ops` index, driving every conflict-detection and semantic-search query.
- **Agent Skills Repo** — 7 skills from `cockroachlabs/cockroachdb-skills` installed and genuinely used, not just referenced: `designing-application-transactions` directly informed the retry/commit logic in `src/resolution/commit.py`, and `setting-up-local-cluster` was the actual mechanism behind the failure-resilience demo above. Full list and a security-scan finding worth knowing about: [`docs/agent-skills-integration.md`](docs/agent-skills-integration.md).

(A "Managed MCP Server" connection was part of this project's original plan but wasn't actually built into the shipped code — flagged here rather than left in as an unverified claim. The two tools above are both genuinely load-bearing, which is what CockroachDB tool eligibility requires.)

**AWS:**
- **Bedrock** — Titan Text Embeddings V2 for all embeddings; Amazon Nova Lite as the primary conflict-resolution arbiter (Claude Haiku 4.5 as fallback — the exact model CLAUDE.md specified, Claude 3.5 Haiku, wasn't enabled on this account; substituted and flagged).
- **Lambda, Fargate, S3** — full CDK infrastructure-as-code in `infra/`, validated via `cdk synth` (53 resources, least-privilege IAM throughout). Not currently deployed — see `infra/README.md` for why and exactly how to deploy and verify it yourself.

## Project layout

```
src/
  schema/      # database schema, migrations, connection module
  resolution/  # conflict detection, deterministic rules, LLM arbiter, transactional commit, consolidation, decay
  ingestion/   # raw text -> claim -> embedding -> subject_key -> candidate belief
  api/         # MnemosClient: add / search / get_all / history / as_of
  demo/        # Streamlit demo app
tests/         # pytest suite - schema, resolution logic, arbiter (mocked + real), commit, concurrency, ingestion, API
scripts/       # verify_setup, migrate/seed runners, stress test, concurrency test, naive baseline, failure demo, demo CLI
infra/         # AWS CDK app (S3, Lambda, Fargate) - IaC only, see infra/README.md
docs/          # architecture diagram, concurrency/failure-resilience writeups, REVIEW_LOG.md (full build history)
```

## Full build history

Every block of this build, every checkpoint, and every real finding along the way — including bugs caught, design decisions flagged rather than silently made, and what's genuinely blocked pending your input — is in [`docs/REVIEW_LOG.md`](docs/REVIEW_LOG.md). Read the top summary first.
