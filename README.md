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
