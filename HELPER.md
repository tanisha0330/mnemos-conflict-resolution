# HELPER.md — onboarding for a new teammate

You're joining **mnemos**, a hackathon project for the CockroachDB × AWS Hackathon ("Build with Agentic Memory"). Deadline: **Aug 18, 2026, 5:00 PM EDT**. This doc is meant to get you productive without a call — read it top to bottom once, then use it as a reference.

---

## What this project is

Most agentic memory systems (Mem0, Zep, Letta) are append-only logs with a search index bolted on. When two agents disagree about the same fact — a payment API says a refund went through, a support ticket says it's still pending — that disagreement just sits there as two rows, and whichever one retrieval happens to surface first becomes the agent's "truth" for that moment.

mnemos does something different: **every memory write is a claim, attributed to a source with a known authority level, never an unattributed fact.** When two claims about the same subject conflict, they're actively detected (vector similarity) and resolved by a two-stage pipeline — cheap deterministic rules first, an LLM arbiter only when the rules genuinely can't decide — and the losing claim is never deleted, just marked `superseded` with the reasoning attached forever (audit trail, not a bolt-on log).

**The real differentiation, specifically:**
- **vs. Zep** — Zep resolves conflicts by recency alone (newer supersedes older). mnemos considers source authority first, then recency (only on subjects marked `volatile`), then a confidence floor, and only falls back to an LLM's reasoned judgment when none of those apply.
- **vs. Mem0** — Mem0 stores both conflicting claims and leaves reconciliation to whoever reads memory later. mnemos resolves *at write time* and returns only the resolved, canonical answer by default.
- **Concurrency, specifically CockroachDB's role** — this isn't a generic "we used a database" claim. Resolution commits are serializable transactions with optimistic version checks and explicit `SQLSTATE 40001` retry. This has been measured, not assumed: 200 concurrent writers on the same subject, zero lost updates, against the live cluster. A naive read-then-write implementation against local PostgreSQL under the identical load and code silently lost 163/200 updates with **zero errors** — that comparison is the concrete argument for why this matters, not a slide bullet.

**The honest caveat** (worth knowing before you talk to anyone about this project): the README already says this out loud, and you should keep saying it out loud too — mnemos isn't claiming to out-scale Zep or out-integrate Mem0. The bet is narrower: *contradiction is a first-class event a memory system should actively resolve*, not a retrieval-time ambiguity left to the caller. See the "Known problems" section below for two places where the as-built system doesn't fully live up to that pitch yet.

---

## Deadline and current status

**As of 2026-08-14, the build is functionally complete and committed.** 8 commits on `master`, pushed to `origin` (`github.com/tanisha0330/mnemos-conflict-resolution`):

| Commit | Covers |
|---|---|
| `7c9b31f` | Project scaffold — structure, deps, env verification |
| `25fb1c1` | Block 1A — schema, migrations, seed data (5 sources / 3 subjects / 10 beliefs, incl. the deliberate stripe_api vs zendesk_tickets conflict pair), vector index |
| `1ade71c` | Block 1B+1C — resolution rules engine (`detection.py`, `rules.py`, `pipeline.py`), Agent Skills Repo integration, stress test |
| `ea55860` | Block 2A-2C — Bedrock arbiter, transactional commit + retry, concurrency proof (50 & 200 writers) |
| `848d7f5` | Block 3A-3C — ingestion pipeline, retrieval API (`MnemosClient`), consolidation/decay, time-travel |
| `512d987` | Block 4A-4C — AWS CDK (synth-only, not deployed), Streamlit demo app, failure-resilience demo, architecture diagram |
| `69c8d3d` | Block 5A — README rewrite and positioning |
| `9eedeff` | Final REVIEW_LOG risk summary |

**Genuinely done and tested against the real live cluster** (not mocked, except where explicitly noted): conflict detection + deterministic rules, Agent Skills Repo integration + 500-belief stress test, Bedrock arbiter (real Nova Lite / Claude Haiku 4.5 calls, not just mocked tests), transactional commit with real 8-thread and 200-thread concurrency proofs, a naive-baseline comparison against a real local PostgreSQL instance, the ingestion pipeline, the retrieval API including real `AS OF SYSTEM TIME` time-travel, consolidation/decay, the Streamlit demo app (driven via `AppTest`, real widget interactions), a real node-failure demo against a genuine local 3-node cluster (watched live, twice), and a README verified from an actual fresh clone.

**Untested / incomplete, specifically:**
- AWS infra (`infra/`) synthesizes cleanly via `cdk synth` (53 resources, 8 IAM roles) but has **never been deployed**. No `cdk deploy` has been run against a real account.
- The Lambda resolution-worker handler is a stub — dependency packaging for `src/` as a Lambda layer was never attempted.
- Fargate task defs use a placeholder public image; the real demo-agent image hasn't been built or pushed to ECR.
- Blocks 5B (demo video) and 5C (buffer) haven't started — those are explicitly not automated, they're yours/the team's to do.

Full block-by-block detail, including every real bug found and fixed along the way, is in `docs/REVIEW_LOG.md` — read the top summary first, it's written for exactly this purpose.

---

## Setup instructions

Assume zero context. These are the exact steps that have actually been run and verified from a fresh clone (Block 5A checkpoint), plus real friction you're likely to hit on Windows.

1. **Clone and enter the repo.**

2. **Create and activate a virtualenv:**
   ```bash
   python -m venv .venv
   # macOS/Linux
   source .venv/bin/activate
   # Windows (Git Bash)
   source .venv/Scripts/activate
   # Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   # Windows (cmd)
   .venv\Scripts\activate.bat
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   This has installed clean from `requirements.txt` alone with no manual fixes needed, per the Block 5A fresh-clone checkpoint.

4. **Copy `.env.example` to `.env` and fill in real values.** Never commit `.env` — it's already in `.gitignore`, but double-check before every commit anyway (see Ground rules below). Required keys, each with what it's for:

   | Key | What it's for |
   |---|---|
   | `DATABASE_URL` | CockroachDB Cloud connection string (Cloud console → Connect). Format: `postgresql://<user>:<password>@<host>:26257/<database>?sslmode=verify-full` |
   | `AWS_REGION` | AWS region where Bedrock is enabled — this project uses `us-east-1` |
   | `BEDROCK_EMBEDDING_MODEL_ID` | Bedrock model ID for embeddings — this project uses Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) |
   | `BEDROCK_ARBITER_MODEL_ID` | Bedrock model ID for the conflict-resolution arbiter — this project uses Amazon Nova Lite as primary, with a Claude Haiku fallback hardcoded in `src/resolution/arbiter.py` (not env-driven) |

   You'll need your own CockroachDB Cloud cluster and your own AWS credentials with Bedrock model access requested — this project's real `.env` values are not shared in this doc or committed anywhere.

5. **Verify the environment before touching anything else:**
   ```bash
   python scripts/verify_setup.py
   ```
   This checks three things: CockroachDB connectivity, AWS credentials via STS, and Bedrock model access (looks for `titan-embed` / `claude` / `nova` in your account's available models). All three must pass (✅) before you do anything else.

   **Real friction you may hit, both already worked around in the script itself, but worth knowing about:**
   - **Windows console encoding**: the ✅/❌ icons will print as garbled `�` on a default Windows terminal (cp1252). `verify_setup.py` already force-reconfigures stdout to UTF-8, but if you write your own script and see garbled output, this is why — add `sys.stdout.reconfigure(encoding="utf-8")` at the top.
   - **SSL cert verification on Windows**: `psycopg2-binary`'s bundled libpq doesn't reliably read the Windows system cert store via `sslrootcert=system`. `verify_setup.py` already points `sslrootcert` at `certifi`'s CA bundle instead — if you write a new script that connects directly with `psycopg2.connect()`, copy that pattern (see the top of `check_database()` in `scripts/verify_setup.py`).
   - **UUID columns**: any script using raw `psycopg2.connect()` (not going through `src/schema/db.py`) needs its own `psycopg2.extras.register_uuid()` call, or UUID primary keys won't round-trip correctly. This bit twice during the build (Block 1A and again in Block 4C's failure-demo script) — go through `src/schema/db.py`'s connection helper when you can, rather than raw `psycopg2.connect()`.

6. **Apply the schema and seed sample data:**
   ```bash
   python -m src.schema.migrate
   python -m src.schema.seed
   ```
   `migrate.py` is idempotent — safe to re-run, it'll report "No pending migrations" if already applied. **`seed.py` is not idempotent** — its source names (`stripe_api`, `zendesk_tickets`, etc.) are fixed literals under a `UNIQUE` constraint, so re-running it against an already-seeded database will fail with an integrity error. That's expected on the shared dev cluster; it's fine on your own fresh cluster.

7. **Try it:**
   ```bash
   python -m scripts.demo_cli
   # or the full interactive app
   streamlit run src/demo/app.py
   ```

---

## Known problems, ranked by severity

Pulled from `docs/REVIEW_LOG.md`. **Updated 2026-08-15: problems #1, #2, and #4 below are now fixed** — see the 2026-08-15 dated entries in `docs/REVIEW_LOG.md` for the full detail (real measurements, real test runs, real reasoning behind each decision). #3 remains open by deliberate scope choice; #5 was already just informational.

### 1. [FIXED 2026-08-15] The flagship demo conflict sometimes never triggered at all
**What was wrong:** the exact scenario the whole demo is built around — support-agent says "refund is still pending," payment-agent says "refund was processed" — sometimes failed to be detected as a conflict at all. Real cosine similarity between the two claim embeddings measured **0.49 and 0.54** across two separate real runs, straddling Stage 1's `<0.5 = no conflict` threshold almost exactly (`src/resolution/detection.py`).

**Fix:** re-measured the real distribution first (25 live runs total, using randomized order IDs matching the actual demo's generation, not a fixed literal ID) — range 0.4997–0.5761, confirming the boundary problem was real. Lowered `NO_CONFLICT_THRESHOLD` to 0.4, then re-verified with 10 more fresh live runs (all correctly detected, min 0.4997) and a corrected stress test confirming the real conflict/false-positive rate is unchanged (13.8%, matching the original 0.5-threshold baseline — a first attempt showed 24.4%, which turned out to be a stale hardcoded range in the stress test's own synthetic-data generator, not a real effect of the threshold change). 28/28 unit tests passed, live cluster.

**Still worth knowing:** validated against this one claim-pair *type* (a status-word flip) with real but limited sampling. If the demo's phrasing changes, re-run `scripts/measure_conflict_similarity.py` before trusting it live.

### 2. [PARTIALLY ADDRESSED 2026-08-15] "The LLM resolves conflicts" was not quite accurate as built
**What was wrong:** Stage 2's rules (`src/resolution/rules.py`) resolve every conflict where authority tiers differ *before* the arbiter is ever reached (this is structurally guaranteed by the rule's control flow, not just an empirical pattern) — so the arbiter only ever saw equal-authority conflicts, and the old blanket "equal authority → `needs_human=true`" rule meant it could never autonomously commit.

**Fix:** real Bedrock testing (`scripts/instrument_arbiter_escalation.py`, 20 live calls across all 4 verdict types) showed every equal-authority call landing on `needs_human=True` at ≥0.80 confidence — a genuinely confident signal being discarded every time, not the model itself being unsure. Added one narrow, deliberate exception: a confident (`>=0.6`), model-endorsed `refinement` verdict can now autonomously commit even under equal authority, since refinement is structurally non-destructive (adds detail, doesn't assert the older claim was wrong). `contradiction`, `temporal_shift`, and `both_valid` still always escalate under equal authority — see `docs/REVIEW_LOG.md`'s 2026-08-15 entry for the full per-verdict reasoning.

**Still matters for framing:** most real conflicts (contradiction, temporal_shift) still escalate to a human. "The LLM resolves some conflicts autonomously, and recommends for the rest" is the accurate pitch now — not "the LLM resolves conflicts" unqualified, and not the old "never resolves" either. Also: CLAUDE.md's own principles text still states the equal-authority rule as an unconditional blanket — that text is now slightly stale against this one exception, and hasn't been edited (it's outside this repo).

### 3. [Bounded, actionable, NOT STARTED] AWS deployment is unverified
**What's wrong:** `infra/` (CDK, Python) synthesizes cleanly — `cdk synth` exit 0, 53 resources, 8 least-privilege IAM roles, all 3 Fargate services present, checked directly in the generated template. It has never actually been deployed to AWS.

**Why it matters:** judges are said to test "delete and redeploy from scratch" reproducibility. That can't be verified without a real `cdk deploy` cycle.

**What to do if you pick this up:** `infra/README.md` has the exact commands (`cdk deploy` → `cdk destroy` → `cdk deploy` again, confirm it comes back up identically). You'll need real AWS credentials with more than STS-only access — the only credentials available during the build were the account root user, and Fargate services left running unattended cost real money, which is why this was deliberately scoped out. Also: the Lambda handler in `infra/lambda_src/resolution_worker/handler.py` is a stub (no `src/` packaging into a Lambda layer attempted), and Fargate task defs reference a placeholder public image, not a real pushed agent image.

### 4. [FIXED 2026-08-15] `resolutions` table had no row for arbiter "neither" outcomes
**What was wrong:** `resolutions.winner_belief_id` / `loser_belief_id` were `NOT NULL`. When the arbiter genuinely returns `winner="neither"`, there was no valid pair to store, so `commit_contested()` skipped the `resolutions` insert entirely rather than fabricate a winner — leaving only a belief-status change, no reasoning row.

**Fix:** migration `0008_resolutions_nullable_winner_loser.sql` made both columns nullable, with a CHECK constraint (`resolutions_winner_loser_both_or_neither`) enforcing both-null-or-both-set at the DB level. Chose this over a separate no-winner table so the audit trail stays in one place rather than fragmenting across two tables. `commit_contested()` now writes a resolutions row whenever real decision metadata exists, with NULL winner/loser for the true "neither" case — the one legitimate skip case (a bare mark-contested call with no decision behind it at all) is preserved and still tested. `tests/test_commit.py` 7/7 and `tests/test_ingestion_pipeline.py` 5/5 passed, live cluster.

### 5. [Cosmetic, already applied, informational only] Model substitution
CLAUDE.md names "Claude 3.5 Haiku" as the arbiter fallback. That exact model wasn't enabled on the build's AWS account, so `anthropic.claude-haiku-4-5-20251001-v1:0` is used instead throughout (`src/resolution/arbiter.py`'s module docstring documents this). Only matters if submission text names "3.5" specifically — check the README's wording before final submission.

---

## Repo structure

```
src/
  schema/                    # DB schema source of truth
    db.py                    #   connection helper (handles Windows SSL cert + UUID adapter quirks)
    migrate.py               #   idempotent migration runner
    seed.py                  #   one-time seed data loader (NOT idempotent, see Setup step 6)
    migrations/*.sql         #   8 migrations: sources, subjects, beliefs, FK, resolutions, vector index, archived flag, nullable winner/loser
  resolution/                # the actual conflict-resolution pipeline
    detection.py             #   Stage 1: cosine similarity via <=>, thresholds 0.5/0.9
    rules.py                 #   Stage 2: authority tier -> volatile recency (>10x) -> confidence floor -> needs_llm
    arbiter.py                #   Stage 3: Bedrock LLM arbiter, forced tool-use, 4 verdict types, needs_human escalation
    commit.py                 #   serializable transaction commit, 40001 retry, commit_contested() for "neither"
    pipeline.py                #  orchestrates detect -> rules -> (arbiter externally) -> commit
    consolidation.py           #  merges near-duplicate candidate beliefs into canonical
    decay.py                    # TTL-based archival of old superseded beliefs (never deletes)
  ingestion/                 # raw text -> belief
    claim_extraction.py       #  Bedrock plain-text claim extraction
    embeddings.py               # Titan V2 embeddings via invoke_model
    subject_key.py              # entity+attribute heuristic (requires digit in entity id) + LLM fallback
    pipeline.py                 # ingest(): full orchestration, extract->embed->assign->detect->rules->arbiter->commit
  api/
    client.py                 # MnemosClient: add / search / get_all / history / as_of, canonical-only by default
  demo/
    app.py                   # Streamlit demo app (the refund scenario from CLAUDE.md)
tests/                        # pytest suite - one file per module above, plus test_schema.py
scripts/
  verify_setup.py             # run this first, see Setup step 5
  stress_test.py               # 500-belief / 20-subject real-cluster stress test
  concurrency_test.py           # 50/200 concurrent-writer proof, zero lost updates
  naive_baseline.py              # the naive read-then-write comparison against local Postgres
  generate_concurrency_chart.py   # produces docs/concurrency-comparison.png
  failure_demo.py                  # node-kill demo against a local 3-node cluster
  demo_cli.py                       # readable end-to-end CLI walkthrough of the flagship scenario
infra/                        # AWS CDK app - IaC only, NOT deployed (see Known problems #3)
  app.py, mnemos_stack.py, lambda_src/resolution_worker/handler.py (stub), README.md (deploy instructions)
.claude/skills/                # 7 installed CockroachDB Agent Skills (real content, committed, not just referenced)
docs/
  REVIEW_LOG.md                # full build history, every checkpoint, every real bug found - read this
  mnemos-build-sequence.md      # the original 5-day block-by-block plan this was built from
  architecture.md                # Mermaid architecture diagram
  concurrency-comparison.md / .png   # naive-baseline vs CockroachDB comparison
  failure-resilience.md            # node-failure demo transcripts
  agent-skills-integration.md       # which skills, why, and a High Risk security-scan finding on one of them
.env.example                  # required env keys, see Setup step 4
```

---

## Remaining work, in priority order

This reflects actual current state (per `docs/REVIEW_LOG.md`'s top summary), not the original 5-day plan — the plan's Blocks 1B through 5A are all done, and as of 2026-08-15, Known problems #1, #2, and #4 are fixed too.

1. **Spot-check the conflict-detection fix live before recording** (Known problem #1, now fixed) — the fix is real and measured, but only against 25 live samples of one claim-pair type; re-run the trigger flow a few times right before hitting record, same caution as before, lower urgency than before.
2. **Decide the exact pitch wording for the arbiter's role** (Known problem #2, now partially addressed) — "resolves some conflicts autonomously, recommends for the rest" is accurate now; make sure the README/video narration says that, not the old "resolves conflicts" or the even older "never resolves."
3. **Record the demo video** (Block 5B, per `docs/mnemos-build-sequence.md`) — script is in the build sequence doc (0:00 agents intro → 0:30 conflict+resolution → 1:15 fulfillment-agent avoids double-refund → 1:45 time-travel → 2:15 concurrency+failure numbers).
4. **AWS deploy/destroy/redeploy cycle** (Known problem #3, still open — deliberately out of scope for the 2026-08-15 fix run) — needed if judges will actually test live deployment reproducibility; needs real (non-root) AWS credentials and someone willing to own the cost.
5. **Buffer time** (Block 5C) — nothing scheduled, reserved for whatever breaks while doing 1-4.
6. **Cleanup, not urgent:** a throwaway local PostgreSQL instance (port 5434, `C:\Users\dell\pgdata-mnemos-baseline`) and a local 3-node CockroachDB cluster's binary/data (`~/.cockroachdb`) are still on disk from the concurrency and failure-resilience testing. Both are outside the repo, harmless to leave, safe to delete whenever.

---

## Ground rules for collaborating

- **Don't silently change anything documented in CLAUDE.md** (schema, the two-stage resolution order, the LLM-outside-transaction boundary, the 4 verdict types, the escalate-don't-force rule, canonical-only-by-default retrieval) **without flagging it first.** CLAUDE.md is explicit that this project's whole pitch rests on the resolution logic being correct and explainable — several real design decisions during the build (e.g. what ">10x more recent" means numerically, how "near-duplicate canonical beliefs" should actually be interpreted) were deliberately flagged in `docs/REVIEW_LOG.md` rather than silently resolved. Keep that habit.
- **Always verify `.env` is excluded before committing.** It's already in `.gitignore`, but check `git status` after any broad `git add` and double-check file contents if anything looks like it might carry credentials before pushing — this project's `DATABASE_URL` and AWS values are real, live credentials.
- **Re-test the flagship demo conflict scenario multiple times before anyone records a demo take.** Known problem #1 is fixed (threshold lowered to 0.4, re-verified with 25 real live measurements), but the validation is real-and-limited, not a formal guarantee for every possible paraphrase. Run the trigger flow a few times before hitting record, same habit as before, just lower stakes now.

---

## Is anything in REVIEW_LOG.md still unresolved right now?

**As of 2026-08-15: #1, #2, and #4 from the original five findings are fixed** (see the dated 2026-08-15 entries in `docs/REVIEW_LOG.md` for full detail — real measurements, real live-cluster test runs, real reasoning for each decision, not just a status flip). **#3 (AWS deployment) remains open** — deliberately excluded from the 2026-08-15 fix run's scope; still needs a real `cdk deploy`/`destroy`/`deploy` cycle from someone with non-root AWS credentials willing to own the cost. **#5 (model substitution) was always just informational**, no action needed unless submission text names "3.5" specifically.
