import uuid
from datetime import datetime, timezone

import numpy as np
import pytest

from src.resolution.detection import DetectionOutcome, classify_similarity, detect_conflict
from src.schema.db import get_connection


# --- pure boundary tests -------------------------------------------------------

def test_similarity_above_0_9_is_duplicate():
    assert classify_similarity(0.9000001) == DetectionOutcome.DUPLICATE


def test_similarity_exactly_0_9_is_conflict_not_duplicate():
    assert classify_similarity(0.9) == DetectionOutcome.CONFLICT


def test_similarity_below_0_5_is_no_conflict():
    assert classify_similarity(0.4999999) == DetectionOutcome.NO_CONFLICT


def test_similarity_exactly_0_5_is_conflict_not_no_conflict():
    assert classify_similarity(0.5) == DetectionOutcome.CONFLICT


def test_similarity_between_bounds_is_conflict():
    assert classify_similarity(0.7) == DetectionOutcome.CONFLICT


# --- DB integration tests -------------------------------------------------------
# Uses unique subject_keys so this doesn't collide with seed.py's data even when
# seeded_db has already run earlier in the same session-scoped database.

def _make_embedding(rng: np.random.Generator) -> list[float]:
    vec = rng.standard_normal(1024)
    return (vec / np.linalg.norm(vec)).tolist()


def _insert_source_and_subject(cur, source_name: str, subject_key: str):
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, source_name, 3, "test source"),
    )
    cur.execute(
        "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
        "VALUES (%s, NULL, 1, 'stable', now())",
        (subject_key,),
    )
    return source_id


def _insert_canonical_belief(cur, subject_key: str, source_id, embedding: list[float]) -> uuid.UUID:
    belief_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO beliefs
            (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, 'canonical claim', %s, 'test-agent', %s, 0.9, now(), 'canonical')
        """,
        (belief_id, subject_key, embedding, source_id),
    )
    cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (belief_id, subject_key))
    return belief_id


def test_detect_conflict_no_canonical(migrated_db):
    subject_key = f"test:detection:no-canonical:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            _insert_source_and_subject(cur, f"src-{uuid.uuid4()}", subject_key)
        conn.commit()

        rng = np.random.default_rng(1)
        result = detect_conflict(conn, subject_key, _make_embedding(rng))
        assert result.outcome == DetectionOutcome.NO_CANONICAL
        assert result.canonical_belief_id is None
    finally:
        conn.close()


def test_detect_conflict_duplicate_for_identical_embedding(migrated_db):
    subject_key = f"test:detection:dup:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        rng = np.random.default_rng(2)
        embedding = _make_embedding(rng)
        with conn.cursor() as cur:
            source_id = _insert_source_and_subject(cur, f"src-{uuid.uuid4()}", subject_key)
            _insert_canonical_belief(cur, subject_key, source_id, embedding)
        conn.commit()

        result = detect_conflict(conn, subject_key, embedding)
        assert result.outcome == DetectionOutcome.DUPLICATE
        assert result.similarity > 0.99
    finally:
        conn.close()


def test_detect_conflict_no_conflict_for_orthogonal_embedding(migrated_db):
    subject_key = f"test:detection:unrelated:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        rng = np.random.default_rng(3)
        canonical_embedding = _make_embedding(rng)
        with conn.cursor() as cur:
            source_id = _insert_source_and_subject(cur, f"src-{uuid.uuid4()}", subject_key)
            _insert_canonical_belief(cur, subject_key, source_id, canonical_embedding)
        conn.commit()

        # a fresh random high-dimensional unit vector is ~orthogonal in expectation
        unrelated_embedding = _make_embedding(np.random.default_rng(999))
        result = detect_conflict(conn, subject_key, unrelated_embedding)
        assert result.outcome == DetectionOutcome.NO_CONFLICT
        assert result.similarity < 0.5
    finally:
        conn.close()
