# Review Log

## Summary — read this first

Autonomous run through the full build sequence, Block 1B → 5A (`docs/mnemos-build-sequence.md`). Block 1A was already done before this run started. Blocks 5B (demo video) and 5C (buffer) are yours, as instructed — I stopped after the Block 5A commit.

**Fully done, tested against the real cluster (not mocked, except where noted):** conflict detection + deterministic rules (Block 1B), Agent Skills Repo integration + stress test (1C), Bedrock arbiter (2A), transactional commit with real 8-thread and 200-thread concurrency proofs (2B, 2C), naive-baseline comparison against a real local Postgres instance (2C), ingestion pipeline (3A), retrieval API incl. real `AS OF SYSTEM TIME` time-travel (3B), consolidation/decay (3C), a real Streamlit demo app driven via `AppTest` (4B), a real node-failure demo against a genuine local 3-node cluster watched twice live (4C), an architecture diagram (4C), and a README tested from an actual fresh clone (5A). AWS infrastructure is written and `cdk synth`-validated (4A) but deliberately not deployed — see below.

**Genuinely blocked / needs your decision, ranked by how much it should worry you before demo day:**

1. **[Highest risk] The flagship demo conflict sometimes never fires at all.** Running the exact CLAUDE.md refund scenario twice through the real Streamlit app, the second run silently failed to detect the conflict — Stage 1's real cosine similarity between "refund is pending" and "refund was processed" for the same order measured **0.49 and 0.54** across two pairs, straddling the spec'd 0.5 no-conflict threshold almost exactly. If this happens live, the whole demo's centerpiece moment just doesn't happen, with no error, no warning — the stale claim silently stays canonical. Full detail and options (none implemented without your say-so, since 0.5/0.9 are literal spec'd values) in the Block 4B entry below. **Test this yourself, more than twice, before you trust it live.**

2. **[High risk] "The LLM resolves conflicts" is not quite accurate as built.** Stage 2's rules resolve every case where authority tiers differ *before* the arbiter is ever reached — so the arbiter only ever sees equal-authority conflicts, and CLAUDE.md's own rule ("equal authority → `needs_human=true`") means it can never autonomously commit a resolution. Every real LLM-arbitrated conflict ends up `contested`, pending a human. If your pitch to judges says the LLM resolves ambiguous cases, the accurate version is "the LLM recommends a resolution for a human to confirm." Decide deliberately how you want to frame this. The Block 2A entry below has the full reasoning and confirms it's a direct, literal reading of CLAUDE.md's own rules, not a bug.

3. **[Bounded, actionable] AWS deployment is unverified.** `infra/` synthesizes cleanly (53 resources, least-privilege IAM throughout, checked directly) but was never actually deployed — scoped that way because the only AWS credentials here are the account root user and Fargate services left running would cost money unattended. The specific checkpoint judges are said to test ("delete and redeploy from scratch") needs you to actually run it once. `infra/README.md` has the exact commands.

4. **[Minor, known gap] `resolutions` has no row for arbiter "neither" outcomes.** `winner_belief_id`/`loser_belief_id` are `NOT NULL` in the Block 1A schema; a true "neither" verdict (which happened twice in real Block 2A testing) has no valid pair to store, so only the belief-status flip is recorded, not a reasoning row. If "full reasoning always kept for audit" is a claim in your submission, this is the one case it doesn't quite cover yet.

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

---
