import numpy as np
import pytest

from src.schema.db import get_connection

TABLES = {"sources", "subjects", "beliefs", "resolutions"}


def test_schema_creates_cleanly(migrated_db):
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                (list(TABLES),),
            )
            found = {row[0] for row in cur.fetchall()}
            assert found == TABLES

            cur.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'beliefs' AND indexname = 'beliefs_embedding_idx'"
            )
            assert cur.fetchone() is not None, "vector index on beliefs.embedding was not created"
    finally:
        conn.close()


def test_seed_data_inserts(seeded_db):
    conn = get_connection(seeded_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sources")
            assert cur.fetchone()[0] == 5

            cur.execute("SELECT count(*) FROM subjects")
            assert cur.fetchone()[0] == 3

            cur.execute("SELECT count(*) FROM beliefs")
            assert cur.fetchone()[0] == 10

            cur.execute("SELECT count(*) FROM resolutions")
            assert cur.fetchone()[0] == 1

            # the deliberate conflict pair: two beliefs on the same subject that
            # disagree, resolved via authority tier (stripe_api beats zendesk_tickets)
            cur.execute(
                """
                SELECT b.status, s.name
                FROM beliefs b JOIN sources s ON s.id = b.source_id
                WHERE b.subject_key = 'refund_status:order-12345'
                """
            )
            rows = cur.fetchall()
            canonical = [r for r in rows if r[0] == "canonical"]
            assert len(canonical) == 1 and canonical[0][1] == "stripe_api"
            superseded = [r for r in rows if r[1] == "zendesk_tickets"]
            assert superseded and superseded[0][0] == "superseded"

            cur.execute(
                "SELECT winner_belief_id, loser_belief_id, verdict FROM resolutions "
                "WHERE subject_key = 'refund_status:order-12345'"
            )
            winner_id, loser_id, verdict = cur.fetchone()
            assert verdict == "contradiction"

            cur.execute("SELECT canonical_belief_id FROM subjects WHERE subject_key = 'refund_status:order-12345'")
            assert cur.fetchone()[0] == winner_id
    finally:
        conn.close()


def test_vector_index_used_for_similarity_query(seeded_db):
    conn = get_connection(seeded_db)
    try:
        query_vec = np.random.default_rng(0).standard_normal(1024)
        query_vec = (query_vec / np.linalg.norm(query_vec)).tolist()

        # Must use <=> (cosine distance) to match the index's vector_cosine_ops
        # opclass: querying with <-> (L2) on a cosine-opclass index falls back to
        # a full scan, since the index only accelerates its own distance metric.
        # The explicit ::vector cast is required: without a target type, psycopg2
        # sends the parameter as a plain decimal[] and <=> can't resolve against it.
        with conn.cursor() as cur:
            cur.execute(
                "EXPLAIN SELECT id FROM beliefs ORDER BY embedding <=> %s::vector LIMIT 5",
                (query_vec,),
            )
            plan = "\n".join(row[0] for row in cur.fetchall())

        assert "full scan" not in plan.lower(), f"expected a vector index scan, got a full scan:\n{plan}"
        assert "vector search" in plan.lower() and "beliefs_embedding_idx" in plan, (
            f"expected a vector search on beliefs_embedding_idx in the plan:\n{plan}"
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM beliefs ORDER BY embedding <=> %s::vector LIMIT 5",
                (query_vec,),
            )
            results = cur.fetchall()
        assert len(results) == 5
    finally:
        conn.close()
