# Review Log

*(Top summary will be written last, once Block 5A is done — see bottom of this file for the running log as it's built.)*

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

---
