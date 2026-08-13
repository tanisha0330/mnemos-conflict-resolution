import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.resolution.consolidation import consolidate_subject
from src.schema.db import get_connection


def _orthonormal_pair(seed: int):
    rng = np.random.default_rng(seed)
    a = rng.standard_normal(1024)
    a /= np.linalg.norm(a)
    b = rng.standard_normal(1024)
    b -= (b @ a) * a
    b /= np.linalg.norm(b)
    return a, b


def _blend(base, orth, similarity):
    v = similarity * base + np.sqrt(1 - similarity**2) * orth
    return (v / np.linalg.norm(v)).tolist()


def _insert_source(cur):
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, f"src-{source_id}", 3, "test"),
    )
    return source_id


def _insert_belief(cur, subject_key, source_id, embedding, claim_text, observed_at, status):
    belief_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO beliefs (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, %s, %s, 'test-agent', %s, 0.8, %s, %s)
        """,
        (belief_id, subject_key, claim_text, embedding, source_id, observed_at, status),
    )
    return belief_id


def test_near_duplicate_candidate_merges_into_canonical(migrated_db):
    subject_key = f"test:consolidate:{uuid.uuid4()}"
    base, orth = _orthonormal_pair(1)
    now = datetime.now(timezone.utc)

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            source_id = _insert_source(cur)
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
            canonical_id = _insert_belief(cur, subject_key, source_id, base.tolist(), "short claim", now - timedelta(hours=1), "canonical")
            cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (canonical_id, subject_key))

            near_dup_embedding = _blend(base, orth, 0.95)
            candidate_id = _insert_belief(cur, subject_key, source_id, near_dup_embedding, "a much longer and more complete restatement of the short claim", now, "candidate")
        conn.commit()

        result = consolidate_subject(conn, subject_key)
        assert result.merged_count == 1

        with conn.cursor() as cur:
            cur.execute("SELECT status, superseded_by FROM beliefs WHERE id = %s", (canonical_id,))
            canonical_status, canonical_superseded_by = cur.fetchone()
            cur.execute("SELECT status FROM beliefs WHERE id = %s", (candidate_id,))
            candidate_status = cur.fetchone()[0]
            cur.execute("SELECT canonical_belief_id FROM subjects WHERE subject_key = %s", (subject_key,))
            new_canonical = cur.fetchone()[0]

        # candidate is longer/more complete -> promoted; old canonical superseded by it
        assert candidate_status == "canonical"
        assert canonical_status == "superseded"
        assert canonical_superseded_by == candidate_id
        assert new_canonical == candidate_id
    finally:
        conn.close()


def test_unrelated_candidate_is_not_merged(migrated_db):
    subject_key = f"test:consolidate:unrelated:{uuid.uuid4()}"
    base, orth = _orthonormal_pair(2)
    now = datetime.now(timezone.utc)

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            source_id = _insert_source(cur)
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
            canonical_id = _insert_belief(cur, subject_key, source_id, base.tolist(), "claim A", now, "canonical")
            cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (canonical_id, subject_key))
            candidate_id = _insert_belief(cur, subject_key, source_id, orth.tolist(), "totally unrelated claim", now, "candidate")
        conn.commit()

        result = consolidate_subject(conn, subject_key)
        assert result.merged_count == 0

        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id = %s", (candidate_id,))
            assert cur.fetchone()[0] == "candidate"
    finally:
        conn.close()
