import threading
import uuid
from datetime import datetime, timezone

import pytest

from src.resolution.commit import CommitResult, StaleResolutionError, commit_contested, commit_resolution
from src.schema.db import get_connection


def _insert_source(cur, tier=3) -> uuid.UUID:
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, f"src-{source_id}", tier, "test source"),
    )
    return source_id


def _insert_subject_with_two_candidates(cur, subject_key: str):
    source_id = _insert_source(cur)
    cur.execute(
        "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
        "VALUES (%s, NULL, 1, 'stable', now())",
        (subject_key,),
    )
    belief_a, belief_b = uuid.uuid4(), uuid.uuid4()
    for belief_id, text in ((belief_a, "claim A"), (belief_b, "claim B")):
        cur.execute(
            """
            INSERT INTO beliefs
                (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
            VALUES (%s, %s, %s, 'test-agent', %s, 0.8, now(), 'candidate')
            """,
            (belief_id, subject_key, text, source_id),
        )
    return belief_a, belief_b


def test_commit_resolution_happy_path(migrated_db):
    subject_key = f"test:commit:happy:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            belief_a, belief_b = _insert_subject_with_two_candidates(cur, subject_key)
        conn.commit()

        result = commit_resolution(
            migrated_db, subject_key, expected_version=1,
            winner_belief_id=belief_a, loser_belief_id=belief_b,
            verdict="contradiction", reasoning="A is right", method="rule", confidence=0.9,
        )
        assert isinstance(result, CommitResult)
        assert result.new_version == 2
        assert result.retries == 0

        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id = %s", (belief_a,))
            assert cur.fetchone()[0] == "canonical"

            cur.execute("SELECT status, superseded_by FROM beliefs WHERE id = %s", (belief_b,))
            status, superseded_by = cur.fetchone()
            assert status == "superseded"
            assert superseded_by == belief_a

            cur.execute("SELECT canonical_belief_id, version FROM subjects WHERE subject_key = %s", (subject_key,))
            canonical_belief_id, version = cur.fetchone()
            assert canonical_belief_id == belief_a
            assert version == 2

            cur.execute(
                "SELECT winner_belief_id, loser_belief_id, verdict, method FROM resolutions WHERE subject_key = %s",
                (subject_key,),
            )
            row = cur.fetchone()
            assert row == (belief_a, belief_b, "contradiction", "rule")
    finally:
        conn.close()


def test_commit_resolution_stale_version_raises_and_changes_nothing(migrated_db):
    subject_key = f"test:commit:stale:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            belief_a, belief_b = _insert_subject_with_two_candidates(cur, subject_key)
        conn.commit()

        with pytest.raises(StaleResolutionError):
            commit_resolution(
                migrated_db, subject_key, expected_version=999,  # wrong on purpose
                winner_belief_id=belief_a, loser_belief_id=belief_b,
                verdict="contradiction", reasoning="A is right", method="rule", confidence=0.9,
            )

        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id = %s", (belief_a,))
            assert cur.fetchone()[0] == "candidate"  # unchanged
            cur.execute("SELECT count(*) FROM resolutions WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT version FROM subjects WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == 1  # unchanged
    finally:
        conn.close()


def test_commit_contested_with_winner_marks_both_contested(migrated_db):
    subject_key = f"test:commit:contested-with-winner:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            belief_a, belief_b = _insert_subject_with_two_candidates(cur, subject_key)
        conn.commit()

        result = commit_contested(
            migrated_db, belief_a, belief_b, subject_key=subject_key,
            winner_belief_id=belief_a, loser_belief_id=belief_b,
            verdict="contradiction", reasoning="tentative", method="llm", confidence=0.55,
        )
        assert result.resolution_id is not None
        assert result.new_version is None

        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id IN (%s, %s)", (belief_a, belief_b))
            statuses = {r[0] for r in cur.fetchall()}
            assert statuses == {"contested"}

            cur.execute("SELECT canonical_belief_id FROM subjects WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] is None  # nothing promoted

            cur.execute("SELECT count(*) FROM resolutions WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_commit_contested_without_winner_skips_resolutions_row(migrated_db):
    """The arbiter's winner="neither" case: no resolutions row can be inserted
    (winner_belief_id/loser_belief_id are NOT NULL) - both beliefs still get
    marked contested, but nothing is fabricated into the audit trail."""
    subject_key = f"test:commit:contested-neither:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            belief_a, belief_b = _insert_subject_with_two_candidates(cur, subject_key)
        conn.commit()

        result = commit_contested(migrated_db, belief_a, belief_b)
        assert result.resolution_id is None

        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id IN (%s, %s)", (belief_a, belief_b))
            statuses = {r[0] for r in cur.fetchall()}
            assert statuses == {"contested"}
    finally:
        conn.close()


def test_concurrent_resolution_attempts_no_lost_updates(migrated_db):
    """N threads race to commit a resolution for the SAME subject, all reading
    the same expected_version, synchronized to start together via a Barrier so
    CockroachDB genuinely sees overlapping transactions (not just one finishing
    before the next starts). Exactly one must win; the rest must fail with
    StaleResolutionError (possibly after real 40001 retries along the way) -
    never silently lose an update or double-apply."""
    subject_key = f"test:commit:concurrent:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            source_id = _insert_source(cur)
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
            belief_ids = []
            for i in range(8):
                belief_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO beliefs
                        (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
                    VALUES (%s, %s, %s, 'test-agent', %s, 0.8, now(), 'candidate')
                    """,
                    (belief_id, subject_key, f"claim {i}", source_id),
                )
                belief_ids.append(belief_id)
        conn.commit()
    finally:
        conn.close()

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results = [None] * n_threads
    errors = [None] * n_threads

    def worker(i):
        barrier.wait()
        try:
            winner = belief_ids[i]
            loser = belief_ids[(i + 1) % n_threads]
            results[i] = commit_resolution(
                migrated_db, subject_key, expected_version=1,
                winner_belief_id=winner, loser_belief_id=loser,
                verdict="contradiction", reasoning=f"thread {i} claims it", method="rule", confidence=0.9,
            )
        except StaleResolutionError as exc:
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r is not None]
    failures = [e for e in errors if e is not None]
    assert len(successes) == 1, f"expected exactly 1 winner, got {len(successes)}"
    assert len(failures) == n_threads - 1
    assert all(isinstance(e, StaleResolutionError) for e in failures)

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM subjects WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == 2  # incremented exactly once, not lost, not double-applied

            cur.execute("SELECT count(*) FROM resolutions WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == 1  # exactly one resolution committed

            cur.execute("SELECT count(*) FROM beliefs WHERE subject_key = %s AND status = 'canonical'", (subject_key,))
            assert cur.fetchone()[0] == 1  # exactly one canonical belief
    finally:
        conn.close()
