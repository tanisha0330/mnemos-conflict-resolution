# Mnemos — Full Build Sequence (Prompts Ready to Paste)
**Status: Block 1A ✅ done (commit 25fb1c1) — schema, migrations, seed data, vector index verified**

Work top to bottom. Each block = one paste into Claude Code. Don't skip the ✋ Checkpoint lines — those are the only manual work required; everything else is copy-paste-run.

---

## DAY 1 — Foundation + Resolution Core

### ✅ Block 1A — Schema (DONE)

### ▶ Block 1B — Conflict detection + deterministic rules
```
Build the first two stages of a belief-conflict resolution pipeline in Python. Stage 1 (conflict detection): given a new belief and its subject_key, query for the current canonical belief on that subject, compute cosine similarity between embeddings using the <=> operator with ::vector cast (per db.py conventions already established). If similarity > 0.9, treat as duplicate (refresh observed_at on existing, discard new). If < 0.5, no conflict (insert as new candidate, unrelated). If between 0.5 and 0.9, flag as a real conflict requiring resolution. Stage 2 (deterministic rules): before calling any LLM, try to resolve using rules in this order: (1) if one source's authority_tier is strictly lower, the higher-tier source wins; (2) if subjects.volatility='volatile' and one claim is >10x more recent, recency wins; (3) if either claim's confidence is below 0.3, it loses automatically to any claim above the floor. If no rule applies, return a clear 'needs_llm' signal, don't guess. Write clear function boundaries so the LLM stage can be plugged in later. Include unit tests covering all rule branches and boundary conditions (similarity exactly 0.5, 0.9; confidence exactly 0.3). Update CLAUDE.md's progress log if one doesn't exist yet — add one at the bottom noting Block 1A and 1B complete.
```
**✋ Checkpoint:** Walk through 2-3 conflict examples yourself (including the seeded order-12345 pair) and check the rule engine's decision makes sense to you as a human, not just that tests pass.

### ▶ Block 1C — Agent Skills Repo + stress test
```
Integrate the CockroachDB open-source Agent Skills Repo into this project (third CockroachDB tool for hackathon eligibility, alongside MCP Server and Distributed Vector Indexing). Research what skills are relevant to our schema/performance work and wire up whichever apply — document exactly what was integrated and how in docs/. Separately, write a stress test script (scripts/stress_test.py) that inserts 500 beliefs across 20 subjects with realistic conflict rates (~15% should trigger Stage 1 conflict detection), and report: total insert time, % resolved without needing an LLM call, and any errors.
```
**✋ Checkpoint:** Confirm Skills Repo integration is real, not just a README mention — you'll need to honestly list it in submission tags.

**→ Commit:** `"Block 1B+1C: resolution rules engine, Agent Skills Repo integration, stress test"`

---

## DAY 2 — LLM Arbiter + Concurrency

### ▶ Block 2A — Bedrock arbiter
```
Build the LLM arbitration stage using Amazon Bedrock (Nova Lite as primary, per BEDROCK_ARBITER_MODEL_ID in .env — use Claude 3.5 Haiku as fallback if Nova's JSON reliability proves poor in testing). Given two conflicting beliefs (claim text, source, authority_tier, observed_at, confidence) and any prior canonical belief on the subject, return strict JSON: {"winner": "A"|"B"|"neither", "verdict": "contradiction"|"refinement"|"temporal_shift"|"both_valid", "reasoning": string, "confidence": float, "needs_human": bool}. needs_human=true whenever confidence < 0.6 OR sources have equal authority_tier OR the model is uncertain. Use Bedrock's structured output/tool-use if available, not prompt-and-hope. Retry logic for malformed JSON (max 2 retries, then fall back to needs_human=true). Write tests using mocked Bedrock responses covering all 4 verdict types and escalation.
```
**✋ Checkpoint:** Read the actual prompt template. Run it against 3-4 real (non-mocked) conflict examples and read the reasoning text yourself — this is your resolution engine's voice, make sure it's not generic.

### ▶ Block 2B — Transactional commit logic
```
Implement the resolution commit step as a CockroachDB serializable transaction. Given a resolution decision (winner, loser, verdict, reasoning), the transaction must: (1) check subjects.version hasn't changed since read (optimistic concurrency), (2) update winner to status='canonical', (3) update loser to status='superseded' with superseded_by set, (4) update subjects.canonical_belief_id and increment version, (5) insert into resolutions with full reasoning. Implement real retry-with-backoff specifically for error code 40001 (CockroachDB serialization failure) — not a generic try/except. The Bedrock arbiter call (Block 2A) must happen BEFORE this transaction opens, never inside it — the transaction only wraps fast DB writes. Write tests simulating concurrent resolution attempts on the same subject verifying no lost updates and correct 40001 retry behavior.
```
**✋ Checkpoint:** Read the transaction function yourself and confirm no network/LLM calls are inside the `BEGIN`/`COMMIT` boundary. This is a specific claim for your submission — verify it's literally true in code.

### ▶ Block 2C — Concurrency proof + baseline comparison
```
Write a load-testing script (scripts/concurrency_test.py) using asyncio/threading that spins up 50 (then 200) concurrent writers attempting conflicting beliefs on the same subject simultaneously, calling the full pipeline (Stage 1 → 2 → arbiter if needed → commit). Assert zero lost updates: final canonical belief and resolutions table must match what serial execution would produce. Then build a second, realistic-but-naive baseline (read-then-write without serializable isolation, no retry) against the same schema on a throwaway local Postgres instance, showing lost updates under identical concurrent load. Produce a comparison report (numbers + simple matplotlib chart, saved to docs/) showing CockroachDB = 0 lost updates vs naive = N lost updates, at both 50 and 200 writers.
```
**✋ Checkpoint:** Sanity-check the naive baseline is realistic ("what most teams would actually build"), not a rigged strawman — this comparison goes in your README and demo video, it needs to hold up.

**→ Commit:** `"Block 2A-2C: Bedrock arbiter, transactional commit, concurrency proof"`

---

## DAY 3 — Full Memory System Layer

### ▶ Block 3A — Ingestion pipeline
```
Build an ingestion pipeline (src/ingestion/) that takes raw text plus metadata (agent_id, source_id) and produces a belief: extract a clear claim_text via Bedrock, generate embedding via Titan Text Embeddings V2, assign/infer subject_key (simple heuristic first — entity+attribute extraction — with LLM fallback for ambiguous cases), insert as candidate belief triggering the Stage 1 conflict pipeline from Block 1B. Write tests with realistic examples across the customer-support demo domain (orders, refunds, shipping status).
```
**✋ Checkpoint:** Spot-check 5 ingested examples — does subject_key assignment make sense? Wrong assignment silently breaks conflict detection downstream.

### ▶ Block 3B — Retrieval API
```
Build a clean Python client API (src/api/) with methods matching common memory-library conventions: add(text, agent_id, source_id), search(query, subject_key=None, limit=10), get_all(subject_key), history(subject_key) [full belief history including superseded, with resolution reasoning], as_of(subject_key, timestamp) [uses AS OF SYSTEM TIME]. Critically: search() and get_all() return ONLY canonical beliefs by default (include_superseded=True flag to override). Write README API usage examples. Write integration tests against the real cluster.
```
**✋ Checkpoint:** Call `search()` yourself on the order-12345 subject and confirm you get one clean canonical answer, not both conflicting claims.

### ▶ Block 3C — Consolidation, decay, time-travel polish
```
Add: (1) consolidation job merging near-duplicate canonical beliefs (similarity >0.9) on the same subject, keeping most complete/recent, (2) decay policy — superseded beliefs older than a configurable TTL get archived=true (never delete, just deprioritize from default queries), (3) polish as_of() to return a clean object: canonical belief at that time + any superseded beliefs with resolution reasoning, formatted for direct demo UI use. Write scripts/demo_cli.py that seeds a realistic conflict scenario, resolves it, and prints an as_of() result readably — this becomes the backbone of the live demo.
```
**✋ Checkpoint:** Run `demo_cli.py` yourself end to end. If it's confusing to read now, fix now — this is close to what judges see.

**→ Commit:** `"Block 3A-3C: ingestion, retrieval API, consolidation/decay/time-travel"`

---

## DAY 4 — Deploy + Demo App

### ▶ Block 4A — AWS deployment
```
Deploy on AWS: package the resolution worker (Stage 1-2-arbiter-commit pipeline) as a Lambda function triggered by new belief inserts, deploy 3 demo agents on Fargate as long-running tasks, set up S3 for source artifacts referenced by beliefs (provenance link via source_id metadata). Use least-privilege IAM roles, not broad admin access. Write as Infrastructure-as-Code (CDK, since we're already in Python) in infra/ so it's reproducible via one command, not console click-ops.
```
**✋ Checkpoint:** Delete and redeploy from scratch once to confirm the IaC is genuinely reproducible — this is exactly what judges test.

### ▶ Block 4B — Demo application
```
Build a Streamlit demo app (src/demo/) simulating the e-commerce refund scenario: payment-agent (mock Stripe API), support-agent (mock Zendesk ticket text), fulfillment-agent (reads memory to decide on refund). Show: (1) live memory state for an order, (2) trigger button causing payment-agent and support-agent to write conflicting claims, (3) resolution happening with reasoning displayed live, (4) fulfillment-agent reading canonical state and correctly NOT double-refunding, (5) a time-travel input showing as_of() state at any point including superseded beliefs and why they lost.
```
**✋ Checkpoint:** Walk the entire demo yourself as if you were a judge seeing it cold for the first time. Fix anything confusing immediately — highest-leverage remaining work.

### ▶ Block 4C — Failure demo + architecture diagram
```
Write a script/instructions demonstrating CockroachDB surviving a node failure without memory downtime (kill a node in a multi-node local cluster, or use CockroachDB Cloud's regional failure simulation if available; show reads/writes continuing). Generate a Mermaid architecture diagram showing: ingestion → Stage 1/2 rules → Bedrock arbiter → transactional commit → retrieval API, with CockroachDB and AWS components clearly labeled. Save to docs/.
```
**✋ Checkpoint:** Watch the failure demo succeed twice live before trusting it for Day 5 recording — this is the single highest-risk live-demo moment.

**→ Commit:** `"Block 4A-4C: AWS deployment, demo app, failure demo, architecture diagram"`

---

## DAY 5 — Ship

### ▶ Block 5A — README + positioning
```
Write the full submission README.md. Open with a one-paragraph pitch, then a positioning comparison table (Zep: time-based supersession, no true conflict resolution; Mem0: attribution only, stores both conflicting claims; Mnemos: rule+LLM resolution with DB-guaranteed consistency). Include: architecture diagram, setup instructions reproducible from a clean clone (test this), API usage examples, concurrency proof numbers from Block 2C, and a "Built With" section listing CockroachDB tools (MCP Server, Distributed Vector Indexing, Agent Skills Repo) and AWS services used.
```
**✋ Checkpoint:** Test setup instructions from an actual fresh clone if possible. Rewrite the positioning section in your own words/voice — this is the first thing judges read.

### Block 5B — Demo video (you do this, not Claude Code)
Script and record the 3-minute demo:
- 0:00–0:30 — agents intro, live memory state
- 0:30–1:15 — conflict occurs, resolution + reasoning shown
- 1:15–1:45 — fulfillment-agent avoids double-refund
- 1:45–2:15 — time-travel rewind with superseded belief + reasoning
- 2:15–3:00 — concurrency proof numbers + node failure survival

3-4 takes. Claude Code can debug anything that breaks mid-recording, but narration is yours.

### Block 5C — Buffer
Nothing scheduled. Something will be broken — this time is for that.

### Submit
Target: 8+ hours before the Aug 18, 5:00 PM EDT deadline.

---

## Quick reference — remaining ✋ Checkpoints (don't skip these)
1. Block 1B — trace conflict examples by hand, agree with rule outputs
2. Block 2A — read arbiter prompt, validate real (non-mocked) reasoning
3. Block 2B — confirm LLM call is genuinely outside the transaction
4. Block 3B — confirm canonical-only retrieval on a real conflict
5. Block 4B — walk the demo as a judge would
6. Block 4C — failure demo succeeds twice live, watched by you
