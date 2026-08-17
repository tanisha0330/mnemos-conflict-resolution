# Review Log

## Summary — read this first

Autonomous run through the full build sequence, Block 1B → 5A (`docs/mnemos-build-sequence.md`). Block 1A was already done before this run started. Blocks 5B (demo video) and 5C (buffer) are yours, as instructed — I stopped after the Block 5A commit.

**Fully done, tested against the real cluster (not mocked, except where noted):** conflict detection + deterministic rules (Block 1B), Agent Skills Repo integration + stress test (1C), Bedrock arbiter (2A), transactional commit with real 8-thread and 200-thread concurrency proofs (2B, 2C), naive-baseline comparison against a real local Postgres instance (2C), ingestion pipeline (3A), retrieval API incl. real `AS OF SYSTEM TIME` time-travel (3B), consolidation/decay (3C), a real Streamlit demo app driven via `AppTest` (4B), a real node-failure demo against a genuine local 3-node cluster watched twice live (4C), an architecture diagram (4C), and a README tested from an actual fresh clone (5A). AWS infrastructure is written and `cdk synth`-validated (4A) but deliberately not deployed — see below.

**Genuinely blocked / needs your decision, ranked by how much it should worry you before demo day:**

1. **[FIXED 2026-08-15, root cause fixed 2026-08-17] The flagship demo conflict sometimes never fired at all.** Was: Stage 1's real cosine similarity between "refund is pending" and "refund was processed" for the same order measured **0.49 and 0.54** across two pairs, straddling the spec'd 0.5 no-conflict threshold. Partially fixed 2026-08-15 by lowering `NO_CONFLICT_THRESHOLD` to 0.4 (25 live runs: min 0.4997, 10/10 correctly detected) — flagged then as "closes the measured gap, not a formal guarantee." That caveat proved correct: a fresh 15-run measurement on 2026-08-17 hit a **real failure** (similarity 0.3690, below even the lowered threshold) 1/15 of the time. Root cause found and fixed at the source, not by moving the threshold further: `claim_extraction.py`'s prompt told the model "if the text already is a clear claim, lightly clean it up rather than rewriting it unnecessarily" — an inconsistent judgment call that occasionally left source jargon (`"Stripe webhook refund.processed for order-X"`) un-normalized instead of rewriting to the same clean form every other run used (`"Refund for order-X was processed"`), pulling that one embedding far enough away to cross the boundary. Prompt now forces one normalized form every time and explicitly forbids passing through source jargon/event names verbatim. Re-measured 20/20 correct post-fix, min similarity **0.594** (vs. the old 0.369 failure) — a real margin, not a coincidence. Full detail in the 2026-08-17 entry below. Full 30-test suite (`test_ingestion_pipeline.py`, `test_claim_extraction.py`, `test_resolution_detection.py`, `test_resolution_rules.py`) re-run clean against the live cluster, no regressions.

2. **[PARTIALLY ADDRESSED 2026-08-15] "The LLM resolves conflicts" was not quite accurate as built.** Was: Stage 2's rules resolve every case where authority tiers differ *before* the arbiter is ever reached (structurally guaranteed by `rules.py`'s control flow — the authority-tier check always runs first and returns before `NEEDS_LLM` is reachable), so the arbiter only ever saw equal-authority conflicts, and the old blanket "equal authority → `needs_human=true`" rule meant it could never autonomously commit. Now: a deliberate, narrow exception was added — a confident (`>=0.6`), model-endorsed `refinement` verdict can autonomously commit even under equal authority, since refinement is structurally non-destructive (adds detail, doesn't assert the older claim was wrong). `contradiction`, `temporal_shift`, and `both_valid` still always force `needs_human=true` under equal authority. Real Bedrock testing backing this decision, and the reasoning for why only `refinement` was exempted, is in the 2026-08-15 entry below. **Framing still matters**: most real conflicts (contradiction, temporal_shift) still escalate — "the LLM resolves some conflicts autonomously, and recommends for the rest" is the accurate pitch now, not "the LLM resolves conflicts" unqualified. Also: CLAUDE.md's own principles text ("equal authority → needs_human=true", stated as a blanket rule) is now slightly stale against this exception — worth a one-line update there if you want the doc to match the code exactly.

3. **[FIXED 2026-08-17] AWS deployment was unverified — now deployed, exercised, torn down, and redeployed for real.** Was: `infra/` synthesized cleanly but was never actually deployed. Now: deployed for real (55 resources), the resolution worker Lambda was wired up for real (was a stub) and invoked against the live cluster where it found and fixed a genuine bug (`pgvector.Vector` not iterable — see the 2026-08-17 entry below), torn down completely, and redeployed from scratch with identical results. **You deployed as the account root user, by your own explicit choice after being asked** — CDK could not assume its own least-privilege bootstrap roles under root and silently fell back to using root credentials directly for asset publishing. Use a scoped IAM identity for any deployment you don't want that caveat on. **Also worth a decision from you**: the deployed Lambda polls and mutates the live shared database every 1 minute for as long as the stack stays up — see the "Operational note" in `infra/README.md` before demo day. Full findings in the 2026-08-17 entry below.

4. **[FIXED 2026-08-15] `resolutions` previously had no row for arbiter "neither" outcomes.** Was: `winner_belief_id`/`loser_belief_id` were `NOT NULL`, so a true "neither" verdict had no valid pair to store and only the belief-status flip was recorded. Fixed via migration `0008_resolutions_nullable_winner_loser.sql` (both columns now nullable, with a CHECK that they're both-null or both-set) and an update to `commit_contested()` in `src/resolution/commit.py` to always write a resolutions row whenever real decision metadata is available, with NULL winner/loser for the true "neither" case. "Full reasoning always kept for audit" now holds for this case too. Full detail in the 2026-08-15 entry below.

5. **[Cosmetic] Model substitution.** CLAUDE.md names "Claude 3.5 Haiku" as the arbiter fallback; that exact model isn't enabled on this AWS account, so `anthropic.claude-haiku-4-5-20251001-v1:0` is used instead throughout. Only matters if your submission text names "3.5" specifically.

**Minor cleanup, not urgent:** a throwaway local PostgreSQL instance (port 5434, `C:\Users\dell\pgdata-mnemos-baseline`) and a local 3-node CockroachDB cluster's binary/data (`~/.cockroachdb`) are still on disk from Blocks 2C and 4C. Both are outside the repo and harmless to leave, safe to delete whenever.

Everything below this line is the full, dated, block-by-block log — every checkpoint, every real bug found and fixed, in the order it happened.

---

## NEEDS HUMAN REVIEW

## 2026-08-14 — Block 4B: the flagship demo conflict sometimes fails to trigger at all (real, reproduced twice)

**This is the single most important thing in this log for you to look at before a live demo.**

Walking `src/demo/app.py` end-to-end (via Streamlit's `AppTest`, which drives the actual app script and real button-click state transitions - not a mock) turned up a real, non-hypothetical reliability problem in the exact scenario CLAUDE.md names as the flagship demo: support-agent says "refund is still pending", payment-agent says "refund was processed" - the textbook case Stage 1 conflict detection is supposed to catch.

**What happened:** ran the app's trigger flow twice. Run 1: worked exactly as intended - Stage 1 correctly classified the two claims as `CONFLICT`, Stage 2's authority-tier rule resolved it, Stripe's claim became canonical, Zendesk's was marked superseded with correct reasoning. Run 2, same code, same scenario, moments later: **the conflict was never detected at all.** Stage 1 classified payment-agent's claim as `NO_CONFLICT` (unrelated), so it was inserted as an independent, unlinked `candidate` belief - no resolution ever ran. Zendesk's stale "still pending" claim stayed canonical. Live memory state showed:
```
🕓 [candidate]  "...was processed."       (Stripe, tier 5)
✅ [canonical]  "...is still pending."    (Zendesk, tier 3)
```
That's the wrong answer sitting as the system's canonical belief, silently.

**Root cause, measured directly, not guessed:** computed real cosine similarity between the two claim embeddings for both runs:
- Run 2's pair: **0.4938** similarity
- A fresh third pair: **0.5431** similarity

Stage 1's thresholds (`src/resolution/detection.py`) are `>0.9` duplicate, `<0.5` no-conflict, `[0.5, 0.9]` real conflict - these exact numbers come from the Block 1B build-doc prompt, not something I chose. **The real embedding similarity between "refund is pending" and "refund was processed" for the same order sits right on top of the 0.5 boundary**, with enough run-to-run variance (from `extract_claim_text()`'s LLM-paraphrased wording differing slightly each call) to land on either side unpredictably. A clear, unambiguous contradiction to any human reader is, to the embedding model, barely distinguishable from an unrelated claim.

**Why I didn't just fix it myself:** 0.5/0.9 are literal values from the build-sequence doc's Block 1B prompt (`"If < 0.5, no conflict... If between 0.5 and 0.9, flag as a real conflict"`), not an implementation detail I get to silently retune - that's exactly the kind of undocumented judgment call CLAUDE.md says to flag rather than quietly resolve. This also isn't a bug in any one function; it's a property of how well Titan V2 embeddings separate this specific *kind* of claim pair (a status flip on an otherwise-identical sentence), which no amount of code review would have caught without actually running it for real, twice, and getting different outcomes.

**What you should decide before demo day** (I did not implement any of these unprompted):
1. Lower the no-conflict threshold (e.g. to 0.4) - straightforward, but is itself an unvalidated guess without more real measurements across many claim-pair types.
2. Add a cheap deterministic supplement ahead of the embedding check specifically for same-subject status-word contradictions (e.g. "pending" vs "processed", "active" vs "cancelled") - more robust for this exact demo, less general.
3. Make the demo's raw input text more contrastive/keyword-preserving so `extract_claim_text()` has less room to paraphrase away the signal - a demo-specific mitigation, doesn't fix the underlying threshold sensitivity.
4. Accept the risk and re-run the demo trigger if it doesn't fire on the first click (workable for a live demo you control, not for an unattended one).

I'd lead with a version of (2) or (3) if it were my call, but this is exactly the kind of decision that affects what you can honestly claim to judges about the system's reliability - flagging for you rather than picking one silently.

---

## 2026-08-14 — Block 4B checkpoint: walked the demo app end-to-end

**Status: PASS on everything except the finding above.** No browser extension available this session, so I used Streamlit's `AppTest` framework instead - it runs the actual `src/demo/app.py` script and simulates real widget interactions (button clicks, selectbox changes) in-process, which is a genuine equivalent to clicking through it, not a weaker substitute. Confirmed, across two full runs:

- Initial page load: correct empty state, correct button enablement, no exceptions.
- Trigger button: both `client.add()` calls execute, "What happened" expanders show both agents' claims.
- fulfillment-agent section: shows the canonical answer (when resolution succeeded) with the "does NOT issue a duplicate refund" message.
- Live memory state: both beliefs listed with correct status icons and confidence.
- Time-travel selectbox: all 3 labeled options present; switching to "before resolution" correctly showed the pre-conflict state (only Zendesk's claim existed yet).

The one real problem found is the conflict-detection reliability issue logged above under NEEDS HUMAN REVIEW - everything else about the app itself (session state, rendering, the time-travel UI) held up under real, repeated interaction.

---

## 2026-08-14 — Block 4A: AWS deployment not verifiable by me (scoped decision, not an oversight)

**What's blocked:** Block 4A's checkpoint — *"Delete and redeploy from scratch once to confirm the IaC is genuinely reproducible - this is exactly what judges test"* — requires an actual `cdk deploy`/`cdk destroy`/`cdk deploy` cycle against a real AWS account. I scoped this block to `cdk synth`-only (no live deploy) per your explicit answer to my clarifying question before this run started: the only AWS credentials available are the account **root user** (not a scoped role), and the spec's Fargate services are meant to be "long-running tasks" that would incur real unattended cost if left up.

**What IS done and verified:** the CDK app (`infra/`) synthesizes cleanly — `cdk synth` exit code 0, 53 resources generated, including all 3 Fargate services/task-defs and 8 distinct least-privilege IAM roles (checked directly in the generated CloudFormation template, not assumed). So the IaC is at least *structurally* sound; what's unverified is whether it actually deploys and comes back up identically after a destroy/redeploy cycle, which only a real account can confirm.

**Exactly what you need to do:** `infra/README.md` has the full deploy instructions, including this exact checkpoint (`cdk deploy` → `cdk destroy` → `cdk deploy` again, confirm it comes back up the same). Also flagged there: the Lambda handler is a stub (dependency packaging for `src/` as a Lambda layer wasn't attempted, since there's nothing to deploy it into), and the Fargate containers use a placeholder public image pending your actual agent image being pushed to ECR.

---

## 2026-08-14 — Block 4C checkpoint: failure demo watched live, twice — PASS

**Status: genuinely done, not worked around.** The original spec offered two paths: a local multi-node cluster, or CockroachDB Cloud's failure simulation. Checked the second option directly rather than assuming: it exists, but only for CockroachDB **Advanced** tier (3+ nodes) — this project's real cluster is **Serverless**, confirmed via `crdb_internal` access being restricted (the literal multi-tenant restriction message). So neither the Cloud option nor Docker (unavailable this session) was viable — but the `setting-up-local-cluster` skill installed back in Block 1C turned out to be exactly the right tool: a genuine 3-node local cluster via the official `cockroach` binary, no Docker, no VM.

Hit one Windows-specific snag getting it running (the skill's `--background` flag isn't supported on Windows builds - each node had to run as its own background process instead) and the same `psycopg2` UUID-adapter gap from Block 1A (this script uses raw `psycopg2.connect()`, needed its own `register_uuid()` call). Both fixed, then ran the actual checkpoint exactly as instructed: **watched the failure demo succeed twice, live**, restarting node 3 between runs. Both runs identical in shape - cluster kept serving throughout via the 2 surviving nodes, only the individual operations that tried to connect directly to the dead node's own address failed (expected), recovering on the very next operation. One real, unsmoothed observation: the first post-kill operation on a surviving node took ~5.5s instead of ~2s both times, consistent with Raft lease transfer for ranges the dead node held — not downtime, but a real latency blip worth knowing about. Full setup and both runs' raw output: `docs/failure-resilience.md`.

Architecture diagram (`docs/architecture.md`, Mermaid) also done: ingestion → Stage 1/2 → arbiter → commit → retrieval API, with CockroachDB and AWS components labeled, cross-referencing the Block 2B transaction-boundary proof and the Block 4A IaC-only status directly in the diagram notes rather than repeating claims that might drift out of sync.

Local cluster cleaned up after the demo (processes stopped); the downloaded binary and data directory remain at `~/.cockroachdb` outside the repo in case you want to re-verify.

---

## 2026-08-14 — Block 5A checkpoint: README tested from a real fresh clone

**Status: PASS.** `git clone .` into a scratch directory (a genuine second checkout, not the working copy) and walked the README's setup steps for real:

1. `python -m venv .venv` + `pip install -r requirements.txt` — clean install, no errors, every dependency resolved from the committed `requirements.txt` alone.
2. Copied `.env` (this project's real credentials, since a hackathon judge would use their own but the *instructions* are what's being tested) and ran `python scripts/verify_setup.py` — all 3 checks passed (CockroachDB, AWS STS, 44 matching Bedrock models).
3. `python -m src.schema.migrate` — correctly reported "No pending migrations," confirming the idempotency claim from Block 1A is still true after 7 migrations.
4. Did **not** re-run `python -m src.schema.seed` against the shared live cluster: `seed.py`'s source names are fixed literals (`stripe_api`, `zendesk_tickets`, etc.) under a `UNIQUE` constraint, so a second run against already-seeded data would correctly fail with an integrity error — expected behavior for a one-time demo seeder, not a setup-instructions bug, but worth knowing: **these instructions assume a genuinely empty database**, which is true for any judge's own fresh cluster but not for a repeated run against this same shared one.

**Positioning section:** rewritten in first-person engineering voice, not lifted from CLAUDE.md's bullet points — deliberately includes an "honest version of this pitch" paragraph acknowledging what mnemos *isn't* claiming, since a positioning table alone reads as marketing regardless of how accurate it is.

**One thing corrected while writing "Built With," not left as an unverified inherited claim:** CLAUDE.md's stack section lists "Managed MCP Server (connected)" as one of the 3 CockroachDB tools used. Checked directly — no `.mcp.json`, no MCP config referencing CockroachDB anywhere in this project. It was never actually wired into the shipped code. Removed from the README's claims rather than repeated uncritically; **Distributed Vector Indexing** and **Agent Skills Repo** are both genuinely, verifiably used (the vector index is load-bearing throughout; two of the installed skills directly informed real code, not just decoration), which still clears the "2+ tools" eligibility bar honestly.

---

## 2026-08-14 — Block 1B checkpoint: hand-traced conflict examples

**Status: PASS.** Ran `src/resolution/rules.apply_rules()` directly (not via tests) against three real/representative conflicts and read the outputs myself.

**Example 1 — the real seeded order-12345 pair** (authority tier rule): `zendesk_tickets` (tier 3, confidence 0.75, observed 14:17:42) vs `stripe_api` (tier 5, confidence 0.98, observed 16:17:42), subject volatility `volatile`.
→ `NEW_WINS`, rule `authority_tier`, reason: *"new source authority_tier=5 outranks existing source authority_tier=3"*.
Matches what was hand-seeded in Block 1A (stripe_api is canonical). Makes sense: a payment processor's API should outrank a human-entered support ticket regardless of anything else.

**Example 2 — recency rule** (constructed): two `internal_db` reports (tier 4, both confidence 0.85) on a volatile shipping-carrier subject, one 10 days stale, one 2 hours fresh.
→ `NEW_WINS`, rule `recency`, reason: *"new claim is 120.0x more recent than existing on a volatile subject (existing age=864000s, new age=7200s)"*.
Makes sense: equal-authority sources, volatile subject (shipping status changes) — freshest wins.

**Example 3 — confidence floor rule** (constructed): two `public_web` claims (tier 1, stable subject) on a user's email, one confidence 0.25 (below floor), one 0.6.
→ `NEW_WINS`, rule `confidence_floor`, reason: *"existing confidence=0.25 is below the floor (0.3); new confidence=0.6 is not"*.
Makes sense: a claim the system itself flagged as low-confidence shouldn't beat a more-confident equal-authority claim without an LLM even being asked.

All three read as correct, non-surprising decisions to a human.

**Design decision flagged (not blocking):** neither CLAUDE.md nor the build-sequence doc defines what "one claim is >10x more recent" actually means numerically. I implemented it as: wall-clock age at evaluation time (`now - observed_at`) for each claim, one side's age must be strictly >10x the other's. This is a judgment call, not something I could derive from the spec — flagging per CLAUDE.md's own instruction to surface undocumented edge-case decisions rather than silently choose. See `src/resolution/rules.py` rule 2 for the exact implementation and boundary handling (tested at exactly 10x and just past 10x in `tests/test_resolution_rules.py`).

**Tests:** 28/28 passed (`tests/test_resolution_rules.py`, `tests/test_resolution_detection.py`, `tests/test_resolution_pipeline.py`), including boundary values at similarity 0.5/0.9 and confidence exactly 0.3.

---

## 2026-08-14 — Block 1C checkpoint: Agent Skills Repo integration

**Status: PASS, real integration confirmed.** Researched what `cockroachlabs/cockroachdb-skills` actually is (real repo, real content — not a hypothetical), installed 7 of its 34 skills via the standard `npx skills add` installer, and verified the result myself: `.claude/skills/*/SKILL.md` files exist on disk with real substantive content (23KB+ each, plus `references/` subdirectories), not stubs. Full rationale for which 7 and why in `docs/agent-skills-integration.md`.

**Concern surfaced and handled, not glossed over:** the installer's own security scan flagged `cockroachdb-sql` **High Risk** (all others Safe/Low/Med). I read the full skill file rather than trusting or discarding it blindly — it's legitimate CockroachDB content with no bundled executable code, but its own instructions direct an invoking agent to autonomously find a connection string and run `cockroach sql` shell commands against it, including on every generated query. Documented this and made an explicit call: keep it installed as reference material (used it to confirm our Block 1A schema already follows its UUID-PK-over-sequential-ID rule), but don't let it autonomously run SQL against `DATABASE_URL` in this project. Full detail in `docs/agent-skills-integration.md`.

**Verified real, not just a README claim:** `.claude/skills/` is committed to this repo (carved a `.gitignore` exception for it, since the rest of `.claude/` is local tool state) — anyone cloning the repo gets the same skills, not just a mention that they exist.

---

## 2026-08-14 — Block 1C: stress test results (`scripts/stress_test.py`)

Ran for real against the live cluster: 500 beliefs across 20 subjects.

```
total_insert_time_seconds: 1994.35  (0.24 beliefs/sec)
outcome_counts: {'needs_llm': 13, 'no_conflict': 301, 'duplicate': 111, 'rule_decided': 53}
total_stage1_conflicts: 66  (conflict_rate_pct: 13.8, target ~15)
resolved_without_llm_pct: 80.3
error_count: 2
```

**Throughput note:** 0.24 beliefs/sec is slow in absolute terms — each iteration does an INSERT round-trip plus the pipeline's own SELECT (Stage 1 similarity, and for conflicts, a second SELECT for Stage 2's rule inputs), all over the internet to a CockroachDB Cloud Serverless cluster, with no batching or connection reuse optimization attempted. This number describes *this unoptimized script's* round-trip latency, not a claim about CockroachDB's own throughput ceiling — not something to quote in the submission as a performance number.

**The 2 errors are expected, not a defect:** both are CockroachDB `RETRY_SERIALIZABLE` / `TransactionRetryWithProtoRefreshError` — i.e. real `SQLSTATE 40001` serialization conflicts under concurrent-ish write load on the same rows. `stress_test.py` deliberately does not implement retry logic, since that's explicitly Block 2B's scope, not Stage 1/2's. Seeing real 40001s here is actually a useful early signal that Block 2B's retry logic will have real conditions to handle, not a hypothetical.

**Conflict-rate accuracy:** 13.8% measured vs. ~15% target — within reasonable variance for a random weighted draw (`p=[0.15, 0.25, 0.60]` over 480 non-seed inserts), no concern.

---

## 2026-08-14 — Block 2A checkpoint: real (non-mocked) arbiter calls

**Status: PASS, after one real fix.** 12/12 mocked tests passed first (all 4 verdict types, escalation rules, retry, fallback, safe-failure — `tests/test_arbiter.py`). Then ran the actual arbiter — real `boto3` calls to `amazon.nova-lite-v1:0` via Bedrock Converse API with forced tool-use, not mocked — against 4 realistic conflict examples, one aimed at each verdict type, and read every reasoning string myself.

**Model substitution flagged:** CLAUDE.md specifies "Claude 3.5 Haiku" as the fallback model, but that exact model isn't in this AWS account's enabled Bedrock models (confirmed against `scripts/verify_setup.py`'s model listing — only `claude-3-haiku-20240307` and `claude-haiku-4-5-20251001` are available, no 3.5). Substituted `anthropic.claude-haiku-4-5-20251001-v1:0` (the modern Haiku) as the fallback. Noted in `src/resolution/arbiter.py`'s module docstring.

**3 of 4 examples were correct and specific on the first try** — e.g. the refinement example: *"Claim B provides a more precise location by adding the apartment number and city, which is not contradicted by Claim A."* References the actual claim content, not a template.

**1 of 4 exposed a real problem, not glossed over:** the temporal_shift example (subscription status "active" 60 days ago vs. "cancelled" yesterday) came back as `verdict=contradiction` instead — the model was collapsing "the world changed" into "one side was wrong," which is exactly the failure mode CLAUDE.md's non-negotiable principles warn against ("4 verdict types... don't collapse these into a simple boolean"). Root cause: the original prompt's one-line definitions of `contradiction` vs. `temporal_shift` didn't give the model a clear enough distinguishing signal. Fixed by rewriting the verdict definitions in `_build_prompt()` with an explicit contrast and worked examples for each. Reran the same example 3 times after the fix: all 3 came back `temporal_shift`, confidence 0.95, with reasoning like *"Claim A was accurate at its time, and Claim B is the current state. This is a temporal shift, not a contradiction."* Consistent across reruns, not a fluke.

**Confirmed consequence of the pipeline's own design (worth your attention, not a bug):** all 4 checkpoint examples used equal authority tiers, because that's the *only* way the real pipeline ever reaches the arbiter — `rules.py`'s Rule 1 (authority tier) already resolves every tier-mismatch case before `NEEDS_LLM` is ever returned. Combined with CLAUDE.md's literal instruction ("needs_human=true... if both sources have equal authority"), this means **`needs_human` will be `True` on every arbiter call that happens through the real pipeline**, every time, by construction — not just in edge cases. All 4 checkpoint runs confirm this (`needs_human=True` in every one). The arbiter's winner/verdict/reasoning are still genuinely useful as a recommendation, but under the current rules, the LLM stage alone can never autonomously commit a resolution — every LLM-arbitrated conflict ends up `status='contested'` pending human review. This is a direct, literal reading of a principle stated identically in both CLAUDE.md and the build-sequence prompt, so I implemented it as written rather than second-guessing it — but flagging clearly here since it's a significant, easy-to-miss behavioral consequence for the demo/judges: **"the LLM resolves ambiguous cases" is not quite accurate as-implemented; "the LLM recommends a resolution for a human to confirm" is.** Worth deciding deliberately whether that's the intended story before demo day.

---

## 2026-08-14 — Block 2B checkpoint: transaction-boundary verification

**Status: PASS, verified in writing as instructed, not assumed.** CLAUDE.md flags this as a specific claim to verify explicitly whenever commit logic is touched, so here's the actual verification: `src/resolution/commit.py`'s import list (lines 19-27) is `random`, `time`, `uuid`, `dataclasses`, `psycopg2.errors`, and `src.schema.db.get_connection` — no `boto3`, no import of `arbiter.py`. Grepped the file for `arbiter|boto3|bedrock`; the only hits are in comments/docstrings *talking about* the arbiter conceptually, never a call to it. This makes the transaction boundary structurally true, not just something I eyeballed in the control flow: the module literally has no access to any network/LLM call from inside `commit_resolution()` or `commit_contested()`.

Line-by-line boundary for `commit_resolution()`: `get_connection()` (L84) → `with conn.cursor() as cur:` (L86) → four `cur.execute()` calls, all plain SQL (L87, 101, 102, 108) → `conn.commit()` (L116). Nothing else runs between the cursor opening and the commit. Both `arbitrate()` (Block 2A) and `commit_resolution()`/`commit_contested()` (this block) take only plain, pre-computed values as arguments — neither function calls the other, and no orchestrating caller exists yet that would put them in the same transaction (that's Block 3A/3B's job, when ingestion actually wires detection → rules → arbiter → commit together; worth re-checking this same boundary claim once that orchestration exists, since that's where it'd actually be possible to introduce the violation).

**Tests:** 5/5 passed against the real cluster (`tests/test_commit.py`), including a genuine concurrency test — 8 threads racing via a `threading.Barrier` to resolve the *same* subject at the *same* expected_version, synchronized to actually overlap (not just run sequentially fast). Result: exactly 1 winner, 7 `StaleResolutionError`s, final DB state has exactly 1 canonical belief, version incremented exactly once, exactly 1 resolutions row — real proof of no lost updates under real concurrent load, not a mocked assertion.

**Schema gap found and flagged, not silently patched:** `resolutions.winner_belief_id`/`loser_belief_id` are `NOT NULL` in the Block 1A schema, but the arbiter can genuinely return `winner="neither"` (happened twice in the 4 real Block 2A checkpoint calls). `commit_contested()` handles this by marking both beliefs `'contested'` and skipping the `resolutions` insert entirely when there's no real winner/loser pair, rather than fabricating one. Tested explicitly (`test_commit_contested_without_winner_skips_resolutions_row`). **This means "neither" outcomes currently have no audit-trail row at all** — only the belief-status change. If the submission's "full reasoning kept for audit" claim needs to cover this case too, the schema would need a nullable-winner variant or a separate table — flagging for your call, not changing the Block 1A schema without confirmation.

---

## 2026-08-14 — Block 2C: full-pipeline concurrency proof (`scripts/concurrency_test.py`)

**Status: PASS, real bug found and fixed along the way.** N concurrent writers (50, then 200) all submit conflicting candidate beliefs for the same subject, synchronized via a `threading.Barrier` so they genuinely overlap, each running the real detect → rules → commit pipeline against the live cluster.

**First run exposed a real gap in the test script itself, not the pipeline under test:** 5 of 200 writer threads crashed with unhandled `psycopg2.errors.SerializationFailure` on the *initial* version-read/belief-insert step, which wasn't wrapped in any retry logic (only the final `commit_resolution()` call was). Those 5 writers silently vanished from the outcome accounting — `outcome_counts` summed to 195, not 200. This didn't threaten the actual "zero lost updates" property (those writers failed safely before ever placing a bid — CockroachDB correctly aborted them rather than corrupting anything), but it was sloppy and would look bad under scrutiny. Fixed by wrapping that step in its own bounded retry loop and adding a hard `assert len(outcomes) == n_writers` so this class of bug can't silently recur. Re-ran after the fix:

```
=== 50 concurrent writers ===
elapsed_seconds: 19.46
outcome_counts: {'lost_at_commit': 49, 'committed_canonical': 1}
final_version: 2, resolutions_count: 1, canonical_belief_count: 1, committed_winners: 1
lost_updates: False

=== 200 concurrent writers ===
elapsed_seconds: 25.26
outcome_counts: {'lost_at_commit': 75, 'committed_canonical': 1, 'no_conflict': 83, 'needs_llm': 41}
final_version: 2, resolutions_count: 1, canonical_belief_count: 1, committed_winners: 1
lost_updates: False
```

All writers accounted for at both scales (50=49+1, 200=75+1+83+41), exactly one winner each time, version incremented exactly once, exactly one resolutions row — real proof of zero lost updates under real concurrent load, not simulated.

**Design choice, stated plainly:** this proof deliberately doesn't route any writer through the real Bedrock arbiter (see the script's module docstring for the full reasoning) — every writer shares the same authority tier, so Stage 2's rule always decides deterministically for whichever writer gets there first, and later writers correctly land in `needs_llm`/`no_conflict` without ever reaching `commit_resolution()`. Block 2A/2B already separately proved the arbiter and `commit_contested()` paths; `commit_contested()` never touches `canonical_belief_id`/`version` at all, so it's structurally incapable of causing a lost update regardless of concurrency. Running 200 live Bedrock calls here would've added real cost and latency without adding concurrency-safety signal.

---

## 2026-08-14 — Block 2C checkpoint: naive-baseline sanity check

**Status: PASS — and the real result is more interesting/honest than the originally-planned "0 vs N" framing.** No Docker available this session (confirmed by an earlier rejected tool call), so per your choice, I installed local PostgreSQL 17 via `winget` for the naive side. Note: winget's silent install left no known superuser password, and I don't have admin rights in this session (`Restart-Service`/`pg_ctl reload` both failed with permission errors) — rather than fight the pre-existing Windows-service instance, I `initdb`'d a **separate, throwaway** PostgreSQL cluster under my own user account on port 5434 (trust auth, local-only), leaving the system-managed service on port 5433 completely untouched. Cleaned up my one failed attempt to reconfigure the service's `pg_hba.conf` (reverted from the backup before giving up on that approach) rather than leaving it in a modified state.

**Sanity-checking the baseline itself (the actual ask of this checkpoint):** the first version of "naive" I considered — no transaction at all — I rejected as too close to a strawman; essentially no team ships code that doesn't wrap a read-modify-write in *some* transaction. The realistic, well-documented, extremely common bug is relying on a database's **default isolation level** without deliberately choosing one: PostgreSQL defaults to `READ COMMITTED`, which does not protect against two concurrent transactions both reading the same row and both committing a write based on that stale read. I ran the **identical Python code** (same transaction, same sleep between read and write, zero retry logic) against both databases, changing nothing but the connection.

**Real, unscripted result — genuinely more interesting than a flat "PostgreSQL bad, CockroachDB good":**

| | 50 writers | 200 writers |
|---|---|---|
| PostgreSQL (READ COMMITTED default) | 50/50 committed, **0 errors**, final value 9 → **41 silently lost** | 200/200 committed, **0 errors**, final value 37 → **163 silently lost** |
| CockroachDB (identical code, SERIALIZABLE default, no retry) | only 2/50 committed, 48 threw real `40001` errors, final value 2 → **0 lost** | only 2/200 committed, 198 errored, final value 2 → **0 lost** |

PostgreSQL's default isolation makes the bug **invisible** — zero errors, silently wrong answer. CockroachDB's default makes the same bug **impossible to hide** — it either works or throws, never corrupts. But CockroachDB's own naive numbers here (2/50, 2/200 succeeding) would look bad in isolation, so the full report (`docs/concurrency-comparison.md`) also re-states Block 2C's *actual* concurrency-proof numbers (this project's real retry logic: all 50/200 writers accounted for, 0 lost either way) right next to it, so the honest full story is: naive code fails safe on CockroachDB but still needs retry logic to be useful — which this project ships.

**Full report:** `docs/concurrency-comparison.md` + `docs/concurrency-comparison.png` (chart generated via `scripts/generate_concurrency_chart.py` from these exact recorded numbers, not fabricated).

**Cleanup note for you:** a throwaway PostgreSQL data directory lives at `C:\Users\dell\pgdata-mnemos-baseline` (server on port 5434, not a Windows service, started manually via `pg_ctl`). Safe to delete/stop whenever — it's outside the repo and unrelated to the project's real database. I'll leave it running for the remainder of this build in case a re-run is needed, and will note its status again in the final summary.

---

## 2026-08-14 — Block 3A checkpoint: subject_key spot-check on 5 real examples

**Status: PASS, after finding and fixing a real bug — exactly the failure mode this checkpoint exists to catch.** Ran 5 realistic, varied raw inputs (Stripe webhook, Zendesk ticket, internal DB snapshot, support chat transcript, warehouse feed) through real (non-mocked) claim extraction and subject_key assignment.

| # | raw input | claim (real Bedrock extraction) | subject_key |
|---|---|---|---|
| 1 | Stripe: charge for order-55019 refunded $89.00 | "Stripe refunded $89.00 for charge ch_1abc for order-55019..." | `refund_status:order-55019` (heuristic) — correct |
| 2 | Zendesk ticket: "package for order 55019 says delivered but never got it" | "The package for order 55019 was marked as delivered but was not received..." | `package_status:order-55019` (LLM fallback) — correct, and note the *same order* (55019) as example 1 correctly lands on a *different* attribute (`refund_status` vs `package_status`) rather than colliding |
| 3 | internal_db: `user_id=902 email_address=...` | "User with ID 902 and email address alex.chen92@gmail.com is verified." | **`user_email:user-with` (heuristic) — WRONG, caught and fixed, see below** |
| 4 | support chat: "subscription for account user-902 was cancelled" | "Subscription for account user-902 was cancelled on 8/10..." | `subscription_status:user-902` (heuristic) — correct |
| 5 | warehouse feed: `SKU BLU-TSHIRT-M quantity_on_hand=0` | "The quantity on hand for SKU BLU-TSHIRT-M is 0." | `quantity_on_hand:sku-blu-tshirt-m` (LLM fallback) — correct |

**The bug (example 3):** the claim extractor phrased it as "User **with** ID 902..." rather than "user-902" — the heuristic's regex `\buser[-_ #]?(\w[\w-]*)` doesn't require the captured token to look like an actual id, so it matched the word "with" immediately following "User ". Result: `user_email:user-with`. Silently wrong — if a second claim about user-902's email had come in phrased normally (like example 4's "user-902"), it would land on a *different* subject_key and the two claims about the same real person would never be compared for conflicts. This is the exact scenario the checkpoint instructions warn about.

**Fix:** `assign_subject_key_heuristic()` now requires the captured entity id to contain at least one digit — every real id in this domain does (`order-12345`, `user-789`, `sku-9012`, `XJ-4471`), but common English words following "user"/"order"/etc. don't. Added a regression test (`test_heuristic_rejects_english_word_as_entity_id`). Re-ran example 3 after the fix: heuristic now correctly returns `None` (falls through to the LLM), which produced `verified:user-902` — the real entity id preserved.

**Tests:** 21/21 passed pre-fix (`tests/test_subject_key.py`, `tests/test_claim_extraction.py`, `tests/test_embeddings.py`, `tests/test_ingestion_pipeline.py` — the last is 5 real, non-mocked end-to-end scenarios against the live cluster: new-subject→canonical, near-duplicate→merge, unrelated claim, differing-authority conflict→rule-resolved, equal-authority conflict→contested), 14/14 after adding the regression test for this fix.

**Scope note:** `ingest()` goes beyond the literal Block 3A text ("triggering the Stage 1 conflict pipeline from Block 1B") — it also wires up the Block 2A arbiter and Block 2B commit steps, so ingestion is a genuinely complete, callable pipeline rather than stopping at a `PipelineResult` nobody acts on. Necessary for Block 4B's demo to have anything to call; not scope creep for its own sake.

---

## 2026-08-14 — Block 3B checkpoint: real `search()` call on order-12345

**Status: PASS.** Also had to reverse-engineer real `AS OF SYSTEM TIME` syntax for `as_of()` before writing it — worth noting since it wasn't what I initially assumed: the table alias must come *before* `AS OF SYSTEM TIME`, not after; a query joining two tables needs the clause written once, after the full `FROM ... JOIN ...` expression (not once per table); and it requires `autocommit=True` on the connection - within a single implicit (non-autocommit) transaction, CockroachDB enforces one consistent historical timestamp across every statement, so a second `AS OF SYSTEM TIME` query on the same non-autocommit connection throws `inconsistent AS OF SYSTEM TIME timestamp`. Verified all of this against the real cluster before writing `client.py`, not assumed from docs.

Called `search()` myself, exactly as instructed, not just trusted the test suite:

```
search("what is the refund status for order 12345?", subject_key="refund_status:order-12345")
  [canonical] 'Refund for order-12345 has been processed and completed' (source=stripe_api, confidence=0.98)
```

One clean answer, not the conflicting zendesk claim. For comparison, `include_superseded=True` on the identical query surfaces all 4 seeded beliefs for the subject, including `[superseded] 'Refund for order-12345 is still pending...'` — confirming the canonical-only default is doing real filtering, not just returning one result by coincidence.

**Tests:** 6/6 passed against the real cluster (`tests/test_api_client.py`), including a real `as_of()` time-travel test that inserts a belief, waits 2 real seconds, captures a timestamp, waits 2 more seconds, changes the canonical belief, then confirms `as_of(midpoint)` returns the old belief and `as_of(now)` returns the new one — genuine time-travel, not simulated.

---

## 2026-08-14 — Block 3C: schema note + checkpoint (`demo_cli.py`)

**Schema evolution (sanctioned, not a silent redesign):** added `beliefs.archived BOOL NOT NULL DEFAULT false` (migration `0007_add_beliefs_archived.sql`) - the build-sequence doc's own Block 3C text requires this for the decay policy, so this is implementing a directed instruction, not an undirected change to the Block 1A schema. Still logging it here for visibility. Operational note: this specific `ALTER TABLE ... ADD COLUMN ... DEFAULT` triggered a real background schema-change job on the cluster ("waiting for job(s) to complete", per the server's own notice) that the `psycopg2`-based migration runner failed against 3 times in a row with `SSL error: unexpected eof while reading` - worked around by applying it once via `psql` directly (which waited for the job correctly), then recording it in `schema_migrations` manually so the migration runner stays idempotent going forward. Not fully root-caused (worth a look if another `ADD COLUMN ... DEFAULT` on a populated table is needed later), but not blocking.

**Consolidation interpretation flagged:** the spec text ("near-duplicate canonical beliefs on the same subject") doesn't quite make sense literally, since only one belief is ever canonical per subject by design. Implemented as the well-defined useful version instead: sweep independent `candidate` beliefs that are >0.9 similar to the current canonical (or to each other), keep whichever is more complete/recent, supersede the other - documented in `src/resolution/consolidation.py`'s docstring.

**Checkpoint: ran `demo_cli.py` myself end-to-end, twice.** First run had a real, visible bug - the exact kind this checkpoint exists to catch: the time-travel section printed a garbled `�` character instead of an em dash, the same Windows-console cp1252 issue fixed in `verify_setup.py` weeks ago but not carried over to this new script. Fixed two ways: added the same `sys.stdout.reconfigure(encoding="utf-8")` guard to `demo_cli.py`, and - more robust - replaced the em dash in `client.py`'s `AsOfResult.pretty()` with a plain hyphen, since that's *library* code other future callers (e.g. Block 4B's Streamlit app, in a different context) shouldn't have to remember to work around. Re-ran; output is now clean and reads clearly end-to-end: conflicting claims in → resolution with reasoning shown → fulfillment-agent correctly avoiding a duplicate refund → a readable time-travel view. This is genuinely close to what judges would see.

---

## 2026-08-15 — Fix for Known Problem #1: flaky conflict-detection threshold

**Measured before touching anything.** Wrote `scripts/measure_conflict_similarity.py` to run the real `extract_claim_text()` → `generate_embedding()` pipeline against the flagship refund-conflict pair, live, with a fresh randomized order ID each run (matching `src/demo/app.py`'s actual `uuid.uuid4().hex[:8]` generation — the earlier "order-12345" in this log was illustrative, not literal). First 10 runs with a fixed literal order ID came back perfectly deterministic (`sim=0.5618` every time — `extract_claim_text` is temperature=0.0 and returned byte-identical text all 10 times), which would have wrongly suggested the bug wasn't real. Switching to randomized order IDs (matching the actual demo) surfaced the real variance: 15 runs gave a range of **0.5045–0.5761** (mean 0.540, std 0.021). So the original root-cause guess (LLM paraphrasing variance) was slightly wrong — the claim text is stable; the variance comes from the random order-ID token itself shifting the embedding. Confirms the original finding (0.49/0.54 straddling 0.5) was real and reproducible, just via a different mechanism than assumed.

**Fix applied:** lowered `NO_CONFLICT_THRESHOLD` in `src/resolution/detection.py` from 0.5 to 0.4 (this was option (a) from the three laid out in the Block 4B entry above — simplest, and now backed by real measurement rather than a guess). Re-ran 10 more fresh live measurements post-fix: range 0.4997–0.5394, all correctly classified `CONFLICT`. Combined across all 25 real measurements taken during this fix, the observed minimum is 0.4997 — comfortably clear of the new 0.4 floor.

**Upper-bound check (the "did this just move the problem" question):** ran `scripts/stress_test.py` (500 beliefs/20 subjects, live cluster) at the new threshold. First run reported `conflict_rate_pct: 24.4` against a ~15% target — looked like a real false-positive spike, but traced to a bug in the stress test itself, not the threshold change: its synthetic "unrelated" data generator hardcoded `rng.uniform(0.0, 0.49)`, calibrated to the *old* boundary, so ~53 of its own synthetic "no conflict" samples now fell inside the new [0.4, 0.49) conflict band purely as a test-harness artifact (expected leakage roughly matches the observed 45-sample gap over target). Fixed `stress_test.py` to derive its conflict/duplicate/no-conflict ranges from `detection.py`'s real `NO_CONFLICT_THRESHOLD`/`DUPLICATE_THRESHOLD` constants instead of hardcoded literals, so it can't silently drift stale again. Re-ran: **`conflict_rate_pct: 13.8`**, `resolved_without_llm_pct: 80.3` — both identical to the original 0.5-threshold baseline from Block 1C. Confirms the lower threshold does not measurably raise the real conflict/false-positive rate.

**Tests:** updated `tests/test_resolution_detection.py`'s boundary tests for 0.4 (including exact-0.4 and exact-0.9 cases) and a stale `0.5` comment in `scripts/concurrency_test.py`. 28/28 passed (`test_resolution_detection.py`, `test_resolution_rules.py`, `test_resolution_pipeline.py`), against the live cluster.

**Not done, flagged rather than assumed:** 0.4 is validated against this one claim-pair *type* (a status-word flip on an otherwise-identical sentence) with real but limited sampling (25 runs). It is not proven safe for every possible claim pair the demo might generate on the fly — if the demo's phrasing changes, re-run `scripts/measure_conflict_similarity.py` before trusting it live.

---

## 2026-08-15 — Fix for Known Problem #2: arbiter escalation, refinement exception

**Structural confirmation, not just an empirical trend.** `apply_rules()` in `src/resolution/rules.py` checks `authority_tier` first (line 47) and returns immediately whenever the two tiers differ — `RuleOutcome.NEEDS_LLM` is only reachable when authority tiers are already equal (and rules 2/3 also didn't decide it). So "the arbiter only ever sees equal-authority conflicts" is provably true from the code's control flow, not a pattern that happened to hold in testing.

**Empirical stage breakdown**, from the corrected stress test (Known Problem #1 entry above, 480 non-seed inserts, live cluster): of 66 real Stage 1 conflicts, **53 (80.3%) were rules-resolved, 13 (19.7%) reached the arbiter** — and by the structural guarantee above, all 13 are equal-authority. Under the pre-fix rule, all 13 would unconditionally escalate to `needs_human`.

**Direct test of the actual question** ("if the arbiter returns a high-confidence refinement/temporal_shift verdict, should it still be forced to needs_human?"): wrote `scripts/instrument_arbiter_escalation.py`, four hand-built equal-authority (`tier=3` both sides) scenarios, one per verdict type, 5 real live Bedrock calls each (20 total, `amazon.nova-lite-v1:0`), no mocking:

| verdict | confidence range | needs_human (pre-fix) | typical winner |
|---|---|---|---|
| contradiction | 0.85–0.95 | True (forced) | `neither` in 4/5 runs |
| refinement | 0.90–0.95 | True (forced) | `B` (the newer/fuller claim) in 5/5 |
| temporal_shift | 0.90 (flat) | True (forced) | `B` in 4/5, `neither` in 1/5 |
| both_valid | 0.80–0.95 | True (forced) | `neither` in 5/5 |

All 20/20 real calls: confidence ≥ 0.80 (threshold is 0.6), model's own `needs_human` field False every time. So every single real escalation was purely the equal-authority rule overriding a genuinely confident model — not the model itself hedging.

**Decision (made with the user, not unilaterally — flagged per CLAUDE.md's own "flag design decisions" principle before implementing):** relax forced escalation *only* for `refinement`, only when confidence >= 0.6 and the model's own `needs_human` is False. Reasoning: refinement is the one verdict type that's structurally non-destructive — the older claim isn't being asserted wrong, just superseded by a fuller restatement of the same fact, so autonomous commit has much lower downside than the other three. `contradiction` keeps forced escalation deliberately: it requires picking a "right" side of a real factual dispute with no independent tiebreaker, and the model itself hedged to `winner=neither` in 1/5 real runs even at 0.85+ confidence, which reads as the model itself being unwilling to fully commit to a side. `temporal_shift` keeps forced escalation because it's an overwrite of state — the same conservative bar the existing volatile-subject 10x-recency rule already applies deliberately. `both_valid` keeps it because it doesn't fit the binary winner/loser `resolutions` schema at all yet (see Known Problem #4).

**Implementation:** `src/resolution/arbiter.py`'s `arbitrate()` — `equal_authority` now only forces escalation unless `verdict == "refinement" and confidence >= CONFIDENCE_ESCALATION_THRESHOLD and not model's own needs_human`. Docstring updated with the reasoning inline, not just in this log. `commit.py`/`ingestion/pipeline.py` needed no changes — they already handle any verdict generically via `decision.needs_human` and `decision.winner`.

**Tests:** 4 new cases in `tests/test_arbiter.py` — confident refinement under equal authority now passes through (`needs_human=False`), low-confidence refinement still escalates, model-flagged-human refinement still escalates, and all three other verdict types still force escalation under equal authority even at 0.95 confidence (parametrized, so this can't silently regress into over-broadening later). 18/18 passed.

**Not done, flagged rather than silently glossed over:** CLAUDE.md's own principles text states the equal-authority rule as an unconditional blanket ("If arbiter confidence < 0.6, or both sources have equal authority, mark status='contested'"). That text is now slightly stale against this one narrow exception. Didn't edit CLAUDE.md itself (it's the user's personal global config, outside this repo) — flagging here instead so the user can decide whether to update it.

**[UPDATE 2026-08-17]** User decided to update it. CLAUDE.md's "Escalate, don't force" principle now states the exception explicitly (refinement verdict, confidence >= 0.6, model's own `needs_human` False, only under equal authority) and keeps the default posture unconditional for `contradiction`/`temporal_shift`/`both_valid`, so it can't be misread as a general loosening later. That file lives outside this repo (`~/.claude/CLAUDE.md`, not under git), so the edit itself isn't a commit in this repository — this log entry is.

---

## 2026-08-15 — Fix for Known Problem #4: audit trail for "neither" verdicts

**Design decision (schema change to a file CLAUDE.md calls "source of truth"), made directly rather than left as an open question — the user authorized taking the recommended call on remaining open items this run, short of anything involving an actual deployment.** Chose nullable `winner_belief_id`/`loser_belief_id` over the alternative HELPER.md floated (a separate table for no-winner outcomes). Reasoning: a "neither" outcome is still fundamentally a resolution decision on a subject — same shape (subject_key, verdict, reasoning, method, confidence, resolved_at), just without a winner/loser pair — so splitting it into a second table would duplicate the shape and fragment the audit trail across two places anyone auditing history has to know to check both of. A CHECK constraint (`resolutions_winner_loser_both_or_neither`) enforces both-null-or-both-set at the database level, not just in application code, so a half-filled row (a real bug) is rejected by the schema itself.

**Migration:** `0008_resolutions_nullable_winner_loser.sql` — `ALTER COLUMN ... DROP NOT NULL` on both columns, then the CHECK constraint. Applied cleanly to the live cluster (`python -m src.schema.migrate`, 1 migration applied, no manual `psql` workaround needed this time, unlike the Block 3C `ADD COLUMN ... DEFAULT` case).

**Code change:** `commit_contested()` in `src/resolution/commit.py` previously only inserted a `resolutions` row when both `winner_belief_id` and `loser_belief_id` were provided — silently dropping the row for a true "neither" outcome. Now it inserts whenever real decision metadata exists (`subject_key` and `verdict` provided), regardless of whether winner/loser are set, with NULL winner/loser for "neither". Kept the original skip behavior for the one legitimate case where skipping is still correct: a bare "just mark these two contested" call with no decision metadata behind it at all (no arbiter/rules verdict exists to record) — this call shape is exercised directly by `tests/test_commit.py::test_commit_contested_with_no_decision_metadata_skips_resolutions_row`, renamed from its old, now-inaccurate name (`..._without_winner_skips_resolutions_row`) since that name described the bug this fix removes, not the case it actually tests.

**Tests:** added `test_commit_contested_neither_verdict_writes_resolutions_row_with_null_winner` (the real fix, live cluster: verdict/reasoning/confidence land correctly with NULL winner/loser) and `test_resolutions_check_constraint_rejects_mismatched_winner_loser` (confirms the DB itself, not just app code, rejects a half-filled row). Full suite re-run against the live cluster: `tests/test_commit.py` 7/7 passed (incl. the existing 8-thread real concurrency test, unaffected), `tests/test_ingestion_pipeline.py` 5/5 passed (incl. the real equal-authority-conflict-escalates-to-contested end-to-end scenario, which now exercises the fixed path for real).

**Not done:** Issue 3 (AWS deploy/destroy/redeploy cycle) was explicitly excluded from this run's scope by the user — `infra/` remains `cdk synth`-only, unverified for real deployment. See the top-of-file summary, item 3, unchanged.

---

## 2026-08-17 — Issue 3: real AWS deploy/destroy/redeploy cycle, resolution worker wired up for real

User asked directly for the deploy/verify/teardown/redeploy cycle previously scoped out. Genuinely blocked twice before any AWS command ran, both resolved with the user rather than worked around silently: no AWS credentials existed in this environment at all (user configured them mid-session), and the resulting identity was the account root user, not a scoped IAM identity (user explicitly chose to proceed with root anyway, after being asked).

**Handler implementation, not just IaC.** `infra/lambda_src/resolution_worker/handler.py` was a stub (see Block 4A). Before "confirm it executes end-to-end" could mean anything, it needed real logic: `src.ingestion.pipeline.resolve_pending_candidate()` (new) re-evaluates a `status='candidate'` belief against whatever is *currently* canonical for its subject and resolves it via the same Stage1→Stage2→arbiter→commit path `ingest()`'s conflict branch uses. `list_pending_candidates()` (new) finds the backlog. The handler fetches `DATABASE_URL` from Secrets Manager per invocation (not a plain env var — visible via `lambda:GetFunction` otherwise) and calls both.

**Why 'candidate' rows exist to poll for at all**: `ingest()` resolves every conflict synchronously except one — `PipelineOutcome.NO_CONFLICT` (not conflicting with whatever was canonical *at insert time*) leaves a belief at `'candidate'` indefinitely, since there's currently no belief status for "evaluated, still not conflicting." `resolve_pending_candidate()` rechecks against the *current* canonical (which may have changed since); if still `NO_CONFLICT`, it's a no-op and stays `'candidate'` for the next poll. This is a real, flagged limitation (not solved by inventing a new schema status under deploy-time pressure), not a bug.

**No Docker needed for the Lambda's dependencies.** `psycopg2-binary`, `pgvector`, `SQLAlchemy`, `python-dotenv`, `certifi` all ship manylinux wheels, so `pip install --platform manylinux2014_x86_64 --only-binary=:all: --target infra/lambda_layer/python ...` worked directly from this Windows machine — no CDK Docker bundling. `boto3` deliberately excluded (Lambda's Python 3.12 runtime already provides it). `mnemos_stack.py` now stages `handler.py` + a real copy of `src/` into `infra/.build/` at synth time as the function's own code asset, separate from the dependency layer.

**`cdk synth` validated clean**, then `cdk bootstrap` + `cdk deploy`: 55 resources, ~114s, all `CREATE_COMPLETE`, checked directly via the CFN event log.

**Real bug #1, found by actually invoking it, not by inspection**: every invocation (including the EventBridge schedule's own automatic 1-minute-interval calls, which started firing immediately after deploy) reported `Status: timeout` at exactly the function's configured timeout, with **zero log output**, even for polls with zero pending candidates. Looked exactly like a network problem (e.g. a CockroachDB Cloud IP allowlist blocking Lambda's egress — a real, plausible hypothesis that was seriously considered). It wasn't: staged `print(..., flush=True)` at each handler stage, redeployed, and the real cause was immediate — `resolve_pending_candidate()` is the first code path to re-read an embedding back out of `beliefs` with `register_vector()` active, which hands back a `pgvector.Vector` object, not a plain `list`. `detect_conflict()`'s `list(new_embedding)` raises `TypeError: 'Vector' object is not iterable` on that type — `ingest()`'s own embeddings never hit this because they come fresh from `generate_embedding()`, never round-tripped through the DB. Fixed in `_load_pending_candidate()` (`.to_list()` normalization). Two regression tests added to `tests/test_ingestion_pipeline.py`, both real (live cluster, real Bedrock embeddings, no mocking) — one exercises the NO_CANONICAL promotion path, the other specifically reproduces the original crash scenario (a real pre-existing canonical + a real DB-round-tripped embedding for the new candidate). Both pass.

**Real finding #2, surfaced by the same invoke**: a genuine backlog of 50 `'candidate'` beliefs already existed in the live cluster (accumulated `NO_CONFLICT` leftovers from earlier blocks' stress tests/demos) — not something this deploy created, but the first thing to actually exercise it.

**Real finding #3**: per-candidate latency from Lambda (~5s, fresh `psycopg2.connect()` with `sslmode=verify-full`, no connection reuse across candidates) is much higher than from a local dev machine (~1s). 50 candidates at that rate risked exceeding even a raised timeout under a batch containing several arbiter (Bedrock) calls. Timeout raised `60s → 120s`, `POLL_LIMIT` capped to `20`/invocation via a new Lambda env var — safe either way since each candidate is resolved independently, so a mid-batch timeout just defers the rest to the next poll rather than half-completing a write.

**Real finding #4 — the concurrency-safety mechanism fired for real**: a manual invoke overlapping with the EventBridge schedule's own concurrent invocation produced a genuine `StaleResolutionError` on one candidate ("subject version changed since the decision was made"). This is CLAUDE.md's optimistic-version-check design working exactly as intended under real concurrent access, not a failure — consistent with the Block 2B/2C concurrency proofs, now demonstrated under real infrastructure instead of a synthetic `threading.Barrier`.

**Real finding #5**: deploying as the account root user, CDK could not assume its own bootstrap `file-publishing-role`/`deploy-role` (`current credentials could not be used to assume '...role...', but are for the right account. Proceeding anyway.`) and fell back to using root credentials directly for asset publishing. Deployment still succeeded, but this defeats part of the reason the bootstrap roles exist — a real argument for a scoped IAM identity on any deployment that isn't disposable verification.

**Teardown**: `cdk destroy` — 51 resources deleted, `CREATE_COMPLETE → DELETE_COMPLETE`. Two `DELETE_SKIPPED` by design, not a bug: the S3 bucket (`RemovalPolicy.RETAIN` — provenance artifacts should never auto-delete) and the three Fargate task defs' CloudWatch log groups (CDK's own ECS log-group default).

**Redeploy from scratch**: `cdk deploy` again — identical 55 resources, `CREATE_COMPLETE`. One CLI-display anomaly worth flagging so it isn't mistaken for a real regression: the `aws-cdk` CLI's own printed "Deployment time: 6304.78s" (~105 minutes) did not happen — verified directly against CloudFormation's own `StackEvents`/`describe_stacks` timestamps, which show the real deploy took ~2 minutes, consistent with the first one. Trust CloudFormation's timestamps over the CLI's printed summary if they ever disagree.

**[UPDATE 2026-08-17, later same session]** Initially left deployed per the task's own final instruction. Flagged the operational risk (EventBridge polling the live shared DB every minute, unattended) directly to the user and recommended tearing it down until actually needed for the demo, since the full cycle is now proven to take ~2 minutes and just work. User agreed — `cdk destroy` run again, stack is not currently deployed. Redeploy when actually needed (live demo or judge verification), not before.

`infra/README.md` fully rewritten to match everything actually needed (prerequisites that were missing, the exact layer-build command, the secret-creation step, and all real findings above) rather than the original synth-only draft.

---

## 2026-08-17 — Root-cause fix for the flagship demo boundary issue (Known Problem #1, follow-up)

User asked directly to fix this after the 2026-08-15 partial fix was flagged in a critical project review as still-open risk ("the flagship demo... has a documented, only-partially-fixed reliability problem").

**Confirmed the risk was real, not hypothetical, with a fresh measurement first** rather than assuming the 2026-08-15 fix still held: 15 live runs of the actual `app.py` raw-text templates (`"Zendesk ticket: customer says refund for order-X is still pending"` / `"Stripe webhook: refund.processed for order-X"`), fresh random order IDs each run, real Bedrock claim extraction + Titan embeddings, no mocking. Result: 14/15 correct, but run 12 measured **0.3690** similarity — below even the already-lowered 0.4 `NO_CONFLICT_THRESHOLD` — and was misclassified `no_conflict`. A real, reproducible failure, not a fabricated concern.

**Found the actual mechanism, not just the symptom**: comparing the outlier run's extracted claim text against every other run's showed the divergence. Every other run normalized claim_b to `"Refund for order-X was processed"`; the failing run's claim_b came back as `"Stripe webhook refund.processed for order-X."` — source jargon (`webhook`, `refund.processed`) passed through nearly verbatim instead of being rewritten. Traced directly to `claim_extraction.py`'s prompt, which told the model *"if the text already is a clear claim, lightly clean it up rather than rewriting it unnecessarily"* — an inherently unstable judgment call (is `"Stripe webhook: refund.processed..."` "already a clear claim"?) that the model didn't answer the same way every time, even at `temperature=0.0`. This is why the 2026-08-15 threshold-lowering fix was necessary but not sufficient: it treated the symptom (where the boundary sits) without addressing the mechanism (why similarity occasionally craters).

**Fix**: rewrote the prompt to force one normalized sentence form every time (`"<Subject> <verb phrase>"`) and explicitly forbid passing through source jargon, field names, or event names verbatim, removing the ambiguous "lightly clean up vs. rewrite" judgment call entirely. `src/ingestion/claim_extraction.py`.

**Re-verified with a fresh 20-run measurement** (same methodology, fresh random order IDs, no mocking): **20/20 correctly classified as conflict**, similarity range 0.594–0.693, mean 0.643 — compare to the pre-fix run's mean of 0.516 and the failure at 0.369. The fix moved the *entire distribution* up with a real margin, not just patched one outlier.

**Regression check**: full 30-test suite re-run against the live cluster (`test_ingestion_pipeline.py`, `test_claim_extraction.py`, `test_resolution_detection.py`, `test_resolution_rules.py`) — 30/30 passed, including `test_near_duplicate_restatement_merges`, which had failed earlier this same session on a real (separate, unrelated) `DUPLICATE_THRESHOLD` boundary tail event — passing now is consistent with that being a rare tail event rather than a persistent break, not evidence this fix touched it (it didn't — different code path, different threshold).

**Not done, flagged rather than silently out of scope**: the `DUPLICATE_THRESHOLD=0.9` boundary is a separate, real fragility (same class of problem, different threshold, different code path — general near-duplicate merging, not the flagship demo) that surfaced once this session as a genuine test failure and was reproduced at a low rate in a 10-run sample (0/10, i.e. not reproduced under that sample, min similarity 0.9643) but is not proven safe the way the flagship path now is. Same root-cause category (extraction-introduced wording variance near a hard threshold) - worth the same treatment if it recurs, not addressed here since it doesn't affect the flagship demo and wasn't what was asked.

---

## 2026-08-18 — Ground-truth verification: a new resolution stage ahead of the heuristic rules

User's critique, verbatim reasoning: for a verifiable transaction (a refund), the system shouldn't only weigh two secondhand claims against each other by source authority - an agent could, and should, be able to check the actual transaction state directly, the way a human investigating a dispute would go look at the real ledger instead of just trusting whichever party has a more credible-sounding job title. Asked directly to fix this, not just document it as a limitation.

**What this is:** a new resolution stage, `src/resolution/verification.py`, that runs *before* Stage 2's authority/recency/confidence heuristics (`src/resolution/rules.py`) for subjects belonging to a verifiable domain. Backed by a real table, not a mock in the pejorative sense - `payment_ledger` (migration `0009_add_payment_ledger_and_verification_method.sql`) is a genuine system-of-record independent of either agent's claim, standing in for what a production system would query live (the real Stripe/processor balance API). For a `refund_status:order-X` subject with a ledger record, the pipeline checks it directly; if the ledger's value clearly matches one claim's wording and not the other's, that claim wins outright - overriding what the authority_tier rule would otherwise have decided, not just tiebreaking it. Falls through to the existing rules/arbiter unchanged for every other subject type, and for refund subjects with no ledger record or an ambiguous match - this can only ever add a decisive answer or do nothing, never override the existing rules with a wrong one, which is what makes it safe to run unconditionally ahead of everything else.

**Why authority_tier alone wasn't good enough, argued concretely, not just asserted:** the existing cascade is winner-take-all with no way for a real fact to override a heuristic proxy for trust. New test `test_ledger_verification_overrides_authority_tier` (`tests/test_ingestion_pipeline.py`) proves this isn't cosmetic: a tier-5 source's claim ("processed") is inserted first and becomes canonical; a tier-2 source's claim ("still pending") arrives second - by `rules.py` alone, authority_tier would make the tier-5 "processed" claim win again, full stop. But the ledger genuinely says "pending". Real result: the **lower-authority** claim wins, the higher-authority one gets superseded, and the resolutions row records `method='ledger_verification'`, `confidence=1.0`, with reasoning naming exactly which claims were confirmed/contradicted and why this bypassed the heuristics. Passed against the live cluster, not asserted from reading the code.

**Schema change** (flagged per CLAUDE.md's own "don't redesign without flagging it," authorized directly by the user's "do whatever is needed"): `resolutions.method` is a real Postgres/CockroachDB `ENUM` (`resolution_method`), not a free-text column - adding `'ledger_verification'` required `ALTER TYPE resolution_method ADD VALUE IF NOT EXISTS 'ledger_verification'`, which applied cleanly via `python -m src.schema.migrate` (no `psql` workaround needed this time, unlike the Block 3C `ADD COLUMN` case). `verdict` stays `'contradiction'` - a ledger-confirmed win is the same shape as a rule-confirmed one (one claim right, one wrong), no new verdict type needed.

**Wired into both places a conflict gets resolved**, not just the synchronous path: `ingest()`'s conflict branch and `resolve_pending_candidate()` (the resolution_worker Lambda's poll loop) both call `verify_against_ledger()` immediately after loading the existing claim, before branching on the rules' outcome.

**Demo wiring**: `src/demo/app.py` now upserts a real `payment_ledger` row (`"processed"`, matching payment-agent's real claim) the moment "Send conflicting claims" is clicked, so the live flagship demo now resolves via ground-truth verification instead of the authority_tier rule - the resolution panel's reasoning text will visibly say "payment ledger... verified state outranks a proxy for trust" instead of "authority_tier outranks," a real, visible behavior change a judge would see live. Seed data (`src/schema/seed.py`) also gained a matching ledger row for the existing seeded order-12345 conflict pair, confirming the same outcome the authority_tier rule already reached there.

**Tests**: `tests/test_verification.py` (6 new tests, real live cluster: non-refund subjects and refund subjects with no ledger record correctly never decide; a real ledger row correctly picks the matching claim in both directions; an ambiguous ledger value correctly falls through rather than guessing; upsert is idempotent) plus the `test_ledger_verification_overrides_authority_tier` end-to-end proof above. One real test-authoring bug caught and fixed along the way: an early version of `test_ledger_confirms_existing_claim_over_new` had the expected winner backwards (asserted `"existing"` when the claim text it wrote actually matched the *new* claim) - the assertion was wrong, not the code; fixed the test data, not weakened the check. Full suite re-run clean: `test_verification.py` 6/6, `test_ingestion_pipeline.py` 8/8 (including this feature's tests), no regressions elsewhere.

**Scope, stated honestly**: this checks a real ledger *table* directly, which is a genuine ground-truth check for the demo's specific domain (refund status) - it is not the more general "give the arbiter live tool-access to re-query any external system" capability the user's original critique also gestured at (that would mean the LLM arbiter itself deciding when and what to verify, across arbitrary domains, not a fixed keyword match against one known table). What's built here is the concrete, narrow, transaction-specific version of that idea - real, tested, and demo-visible - not the fully general one. Worth naming explicitly if a judge asks how far this generalizes.

---
