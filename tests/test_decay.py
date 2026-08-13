import uuid
from datetime import datetime, timedelta, timezone

from src.resolution.decay import apply_decay
from src.schema.db import get_connection


def _insert_source(cur):
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, f"src-{source_id}", 3, "test"),
    )
    return source_id


def _insert_belief(cur, subject_key, source_id, observed_at, status):
    belief_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO beliefs (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, 'claim', 'test-agent', %s, 0.8, %s, %s)
        """,
        (belief_id, subject_key, source_id, observed_at, status),
    )
    return belief_id


def test_apply_decay_archives_old_superseded_beliefs(migrated_db):
    subject_key = f"test:decay:{uuid.uuid4()}"
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
            old_superseded = _insert_belief(cur, subject_key, source_id, now - timedelta(days=200), "superseded")
            recent_superseded = _insert_belief(cur, subject_key, source_id, now - timedelta(days=5), "superseded")
            old_canonical = _insert_belief(cur, subject_key, source_id, now - timedelta(days=200), "canonical")
        conn.commit()

        archived_count = apply_decay(conn, ttl=timedelta(days=90))
        assert archived_count == 1

        with conn.cursor() as cur:
            cur.execute("SELECT archived FROM beliefs WHERE id = %s", (old_superseded,))
            assert cur.fetchone()[0] is True

            cur.execute("SELECT archived FROM beliefs WHERE id = %s", (recent_superseded,))
            assert cur.fetchone()[0] is False

            # canonical beliefs are never archived, regardless of age
            cur.execute("SELECT archived FROM beliefs WHERE id = %s", (old_canonical,))
            assert cur.fetchone()[0] is False
    finally:
        conn.close()


def test_apply_decay_never_deletes(migrated_db):
    subject_key = f"test:decay:no-delete:{uuid.uuid4()}"
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
            belief_id = _insert_belief(cur, subject_key, source_id, now - timedelta(days=365), "superseded")
        conn.commit()

        apply_decay(conn, ttl=timedelta(days=1))

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM beliefs WHERE id = %s", (belief_id,))
            assert cur.fetchone()[0] == 1  # still exists, just archived
    finally:
        conn.close()
