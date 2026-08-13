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
