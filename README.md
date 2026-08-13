# mnemos

Agentic memory system with built-in conflict resolution, built on CockroachDB + AWS Bedrock.

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
client.as_of("refund_status:order-12345", datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc))
# -> {"canonical": Belief(...) as it stood at that moment, "version": ...}
```

## Project layout

```
src/
  schema/      # database schema definitions
  resolution/  # conflict resolution logic
  ingestion/   # memory ingestion pipeline
  api/         # application API layer
  demo/        # demo/UI code
tests/         # test suite
scripts/       # dev/ops scripts (e.g. verify_setup.py)
infra/         # infrastructure config
docs/          # documentation
```
