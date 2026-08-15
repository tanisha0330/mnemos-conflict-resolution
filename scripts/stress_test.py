"""Stress-tests the Stage 1 (detection) + Stage 2 (rules) pipeline: inserts 500
beliefs across 20 subjects with a realistic conflict mix (~15% land in Stage 1's
CONFLICT bucket and proceed to Stage 2 rules), and reports insert throughput,
what fraction of conflicts were resolved without an LLM call, and any errors.

This benchmarks src/resolution/{detection,rules,pipeline}.py against real
insert volume. It always inserts every belief as a 'candidate' row (rather than
discarding true duplicates, which is production ingestion behavior owned by
Block 3A) so the throughput numbers reflect the full realistic data volume.

Usage: python -m scripts.stress_test [DATABASE_URL]
"""

import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from src.resolution.detection import DUPLICATE_THRESHOLD, NO_CONFLICT_THRESHOLD
from src.resolution.pipeline import PipelineOutcome, evaluate_new_belief
from src.resolution.rules import RuleCandidate
from src.schema.db import get_connection

NUM_SUBJECTS = 20
NUM_BELIEFS = 500
EMBEDDING_DIM = 1024

# realistic-ish mix: most new info is either unrelated to the current canonical
# claim or a restatement of it; genuine conflicts are the minority case.
CONFLICT_WEIGHT = 0.15
DUPLICATE_WEIGHT = 0.25
NO_CONFLICT_WEIGHT = 0.60

AUTHORITY_TIERS = [5, 4, 3, 2, 1]


def _unit_vector(rng: np.random.Generator) -> np.ndarray:
    v = rng.standard_normal(EMBEDDING_DIM)
    return v / np.linalg.norm(v)


def _orthogonal_to(base: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    v = rng.standard_normal(EMBEDDING_DIM)
    v -= (v @ base) * base
    return v / np.linalg.norm(v)


def _blend(base: np.ndarray, orth: np.ndarray, similarity: float) -> list[float]:
    v = similarity * base + np.sqrt(1 - similarity**2) * orth
    return (v / np.linalg.norm(v)).tolist()


def _setup_sources(cur, run_id: str) -> list[tuple[uuid.UUID, int]]:
    sources = []
    for tier in AUTHORITY_TIERS:
        source_id = uuid.uuid4()
        cur.execute(
            "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
            (source_id, f"stress-test-{run_id}-tier{tier}", tier, "stress test source"),
        )
        sources.append((source_id, tier))
    return sources


def _setup_subjects(cur, rng: np.random.Generator, run_id: str, sources) -> dict:
    """Inserts one canonical belief per subject and returns per-subject state
    needed to generate realistic follow-on beliefs."""
    subjects = {}
    for i in range(NUM_SUBJECTS):
        subject_key = f"stress:{run_id}:subject-{i}"
        volatility = "volatile" if i % 2 == 0 else "stable"
        cur.execute(
            "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
            "VALUES (%s, NULL, 1, %s, now())",
            (subject_key, volatility),
        )
        base = _unit_vector(rng)
        orth = _orthogonal_to(base, rng)
        source_id, tier = sources[i % len(sources)]
        belief_id = uuid.uuid4()
        observed_at = datetime.now(timezone.utc) - timedelta(days=1)
        cur.execute(
            """
            INSERT INTO beliefs
                (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
            VALUES (%s, %s, %s, %s, 'stress-test-agent', %s, 0.9, %s, 'canonical')
            """,
            (belief_id, subject_key, f"seed claim for {subject_key}", base.tolist(), source_id, observed_at),
        )
        cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (belief_id, subject_key))
        subjects[subject_key] = {"base": base, "orth": orth, "volatility": volatility}
    return subjects


def run_stress_test(database_url: str | None = None) -> dict:
    run_id = uuid.uuid4().hex[:8]
    rng = np.random.default_rng(42)
    conn = get_connection(database_url)
    errors = []
    outcome_counts = {}
    conflict_rule_decided = 0
    conflict_needs_llm = 0

    try:
        with conn.cursor() as cur:
            sources = _setup_sources(cur, run_id)
            subjects = _setup_subjects(cur, rng, run_id, sources)
        conn.commit()

        subject_keys = list(subjects.keys())
        start = time.perf_counter()

        for _ in range(NUM_BELIEFS - NUM_SUBJECTS):  # NUM_SUBJECTS beliefs already inserted as canonical seeds
            subject_key = subject_keys[rng.integers(0, len(subject_keys))]
            state = subjects[subject_key]
            volatility = state["volatility"]

            category = rng.choice(
                ["conflict", "duplicate", "no_conflict"],
                p=[CONFLICT_WEIGHT, DUPLICATE_WEIGHT, NO_CONFLICT_WEIGHT],
            )
            # ranges derived from the real Stage 1 thresholds (src/resolution/detection.py)
            # so this stays correct if those thresholds ever change, instead of drifting stale.
            if category == "conflict":
                similarity_target = float(rng.uniform(NO_CONFLICT_THRESHOLD, DUPLICATE_THRESHOLD))
                embedding = _blend(state["base"], state["orth"], similarity_target)
            elif category == "duplicate":
                embedding = _blend(state["base"], state["orth"], float(rng.uniform(DUPLICATE_THRESHOLD + 0.01, 0.999)))
            else:
                embedding = _blend(state["base"], state["orth"], float(rng.uniform(0.0, NO_CONFLICT_THRESHOLD - 0.01)))

            source_id, tier = sources[rng.integers(0, len(sources))]
            confidence = float(rng.uniform(0.2, 0.99))
            observed_at = datetime.now(timezone.utc) - timedelta(hours=float(rng.uniform(0, 48)))

            try:
                belief_id = uuid.uuid4()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO beliefs
                            (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
                        VALUES (%s, %s, %s, %s, 'stress-test-agent', %s, %s, %s, 'candidate')
                        """,
                        (belief_id, subject_key, f"claim on {subject_key} ({category})", embedding, source_id, confidence, observed_at),
                    )
                conn.commit()

                result = evaluate_new_belief(
                    conn, subject_key, embedding,
                    RuleCandidate(authority_tier=tier, confidence=confidence, observed_at=observed_at),
                    volatility,
                )
                outcome_counts[result.outcome.value] = outcome_counts.get(result.outcome.value, 0) + 1
                if result.outcome == PipelineOutcome.RULE_DECIDED:
                    conflict_rule_decided += 1
                elif result.outcome == PipelineOutcome.NEEDS_LLM:
                    conflict_needs_llm += 1
            except Exception as exc:  # noqa: BLE001 - stress test must keep going and report all errors
                conn.rollback()
                errors.append(str(exc))

        elapsed = time.perf_counter() - start
    finally:
        conn.close()

    total_conflicts = conflict_rule_decided + conflict_needs_llm
    pct_resolved_without_llm = (conflict_rule_decided / total_conflicts * 100) if total_conflicts else 0.0

    return {
        "total_beliefs_inserted": NUM_BELIEFS,
        "total_insert_time_seconds": round(elapsed, 2),
        "beliefs_per_second": round((NUM_BELIEFS - NUM_SUBJECTS) / elapsed, 2) if elapsed else None,
        "outcome_counts": outcome_counts,
        "total_stage1_conflicts": total_conflicts,
        "conflict_rate_pct": round(total_conflicts / (NUM_BELIEFS - NUM_SUBJECTS) * 100, 1),
        "resolved_without_llm_pct": round(pct_resolved_without_llm, 1),
        "needs_llm_count": conflict_needs_llm,
        "error_count": len(errors),
        "errors_sample": errors[:5],
    }


if __name__ == "__main__":
    database_url = sys.argv[1] if len(sys.argv) > 1 else None
    report = run_stress_test(database_url)
    print("Stress test report:")
    for key, value in report.items():
        print(f"  {key}: {value}")
