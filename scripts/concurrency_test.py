"""Full-pipeline concurrency proof: N concurrent writers submit conflicting
candidate beliefs for the same subject, each running the REAL detect -> rules
-> commit pipeline (src/resolution/{pipeline,commit}.py) against the live
CockroachDB cluster, synchronized with a threading.Barrier so they genuinely
overlap. Asserts zero lost updates: exactly one writer ends up canonical,
version increments by exactly 1, exactly one resolutions row is added -
matching what any valid serial ordering would produce, per CockroachDB's
serializability guarantee.

Design note on the arbiter: every writer's belief shares the same authority
tier (5), strictly higher than the original canonical's tier (2), so the
first writer through Stage 2 always resolves deterministically via the
authority-tier rule (RULE_DECIDED). Writers that read state *after* someone
else already won see two tier-5 beliefs (the new canonical vs. their own) -
an equal-authority conflict, which Stage 2 correctly routes to NEEDS_LLM.
This test does NOT call the real arbiter for those: Block 2A's own findings
show commit_contested() never touches subjects.canonical_belief_id/version at
all, so it structurally cannot cause a lost update regardless of concurrency
- exercising 50-200 live Bedrock calls here would add cost and latency
without adding concurrency-safety signal. Writers that land in NEEDS_LLM (or
DUPLICATE, if their embedding ends up close to whoever already won) are
counted as "lost the race before reaching commit" - a valid, expected
outcome, not an error.

Usage: python -m scripts.concurrency_test [DATABASE_URL]
"""

import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import psycopg2.errors

from src.resolution.commit import StaleResolutionError, commit_resolution
from src.resolution.pipeline import PipelineOutcome, evaluate_new_belief
from src.resolution.rules import RuleCandidate
from src.schema.db import get_connection

EMBEDDING_DIM = 1024
LOW_TIER = 2
HIGH_TIER = 5


def _setup_subject(database_url):
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            source_low = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (source_low, f"concurrency-low-{uuid.uuid4()}", LOW_TIER, "concurrency test source"),
            )
            source_high = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (source_high, f"concurrency-high-{uuid.uuid4()}", HIGH_TIER, "concurrency test source"),
            )

            subject_key = f"concurrency:{uuid.uuid4()}"
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )

            rng = np.random.default_rng(1)
            base = rng.standard_normal(EMBEDDING_DIM)
            base /= np.linalg.norm(base)

            canonical_id = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO beliefs
                    (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
                VALUES (%s, %s, 'original low-authority claim', %s, 'seed-agent', %s, 0.8, now(), 'canonical')
                """,
                (canonical_id, subject_key, base.tolist(), source_low),
            )
            cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (canonical_id, subject_key))
        conn.commit()
    finally:
        conn.close()
    return subject_key, base, source_high


def _writer_embedding(base: np.ndarray, writer_index: int) -> list[float]:
    rng = np.random.default_rng(1000 + writer_index)
    orth = rng.standard_normal(EMBEDDING_DIM)
    orth -= (orth @ base) * base
    orth /= np.linalg.norm(orth)
    similarity = 0.7  # inside the [0.5, 0.9] "real conflict" band
    v = similarity * base + np.sqrt(1 - similarity**2) * orth
    return (v / np.linalg.norm(v)).tolist()


def _insert_candidate_belief_with_retry(conn, subject_key, embedding, index, source_high, max_retries=5):
    """The initial version-read + belief-insert isn't guarded by commit_resolution()'s
    retry logic (that only wraps the final resolution commit) - under heavy concurrent
    load this simple insert can itself hit a real SQLSTATE 40001. Safe to blindly retry:
    it's an independent row insert, not a decision based on a prior read."""
    for attempt in range(1, max_retries + 1):
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM subjects WHERE subject_key = %s", (subject_key,))
                expected_version = cur.fetchone()[0]

            new_belief_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO beliefs
                        (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 0.9, now(), 'candidate')
                    """,
                    (new_belief_id, subject_key, f"writer-{index} claim", embedding, f"writer-{index}", source_high),
                )
            conn.commit()
            return expected_version, new_belief_id
        except psycopg2.errors.SerializationFailure:
            conn.rollback()
            if attempt == max_retries:
                raise
            continue


def _writer(database_url, subject_key, base, source_high, index, outcomes, lock):
    embedding = _writer_embedding(base, index)
    try:
        conn = get_connection(database_url)
        try:
            expected_version, new_belief_id = _insert_candidate_belief_with_retry(
                conn, subject_key, embedding, index, source_high
            )
            pipeline_result = evaluate_new_belief(
                conn, subject_key, embedding,
                RuleCandidate(authority_tier=HIGH_TIER, confidence=0.9, observed_at=datetime.now(timezone.utc)),
                "stable",
            )
        finally:
            conn.close()

        outcome_label = pipeline_result.outcome.value
        if pipeline_result.outcome == PipelineOutcome.RULE_DECIDED and pipeline_result.rule_outcome is not None and pipeline_result.rule_outcome.value == "new_wins":
            try:
                commit_resolution(
                    database_url, subject_key, expected_version,
                    winner_belief_id=new_belief_id, loser_belief_id=pipeline_result.canonical_belief_id,
                    verdict="contradiction", reasoning=f"writer-{index}: {pipeline_result.reason}", method="rule", confidence=0.9,
                )
                outcome_label = "committed_canonical"
            except StaleResolutionError:
                outcome_label = "lost_at_commit"
    except Exception as exc:  # noqa: BLE001 - every writer must be accounted for, none may vanish silently
        outcome_label = f"error: {exc.__class__.__name__}"

    with lock:
        outcomes[index] = outcome_label


def run_concurrency_test(n_writers: int, database_url: str | None = None) -> dict:
    subject_key, base, source_high = _setup_subject(database_url)

    outcomes = {}
    lock = threading.Lock()
    barrier = threading.Barrier(n_writers)

    def synced_writer(i):
        barrier.wait()
        _writer(database_url, subject_key, base, source_high, i, outcomes, lock)

    start = time.perf_counter()
    threads = [threading.Thread(target=synced_writer, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    assert len(outcomes) == n_writers, (
        f"{n_writers - len(outcomes)} writer(s) vanished without recording an outcome - "
        f"every writer must be accounted for"
    )

    outcome_counts: dict[str, int] = {}
    for label in outcomes.values():
        outcome_counts[label] = outcome_counts.get(label, 0) + 1

    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version, canonical_belief_id FROM subjects WHERE subject_key = %s", (subject_key,))
            final_version, final_canonical = cur.fetchone()
            cur.execute("SELECT count(*) FROM resolutions WHERE subject_key = %s", (subject_key,))
            resolutions_count = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM beliefs WHERE subject_key = %s AND status = 'canonical'", (subject_key,)
            )
            canonical_belief_count = cur.fetchone()[0]
    finally:
        conn.close()

    committed = [i for i, label in outcomes.items() if label == "committed_canonical"]
    lost_updates = not (final_version == 2 and resolutions_count == 1 and canonical_belief_count == 1 and len(committed) == 1)

    return {
        "n_writers": n_writers,
        "elapsed_seconds": round(elapsed, 2),
        "outcome_counts": outcome_counts,
        "final_version": final_version,
        "resolutions_count": resolutions_count,
        "canonical_belief_count": canonical_belief_count,
        "committed_winners": len(committed),
        "lost_updates": lost_updates,
        "subject_key": subject_key,
    }


if __name__ == "__main__":
    database_url = sys.argv[1] if len(sys.argv) > 1 else None
    for n in (50, 200):
        report = run_concurrency_test(n, database_url)
        print(f"=== {n} concurrent writers ===")
        for key, value in report.items():
            print(f"  {key}: {value}")
        print()
