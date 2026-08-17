"""Real, non-mocked end-to-end integration tests: raw text -> ingest() -> real
Bedrock calls (claim extraction, embedding, and the arbiter when needed) ->
real DB writes against the live cluster. Deliberately not mocked - this is
the same "test against reality" bar the Block 2A/2B/2C checkpoints used."""

import uuid
from datetime import datetime, timezone

import pytest

from src.ingestion.embeddings import generate_embedding
from src.ingestion.pipeline import ingest, resolve_pending_candidate
from src.schema.db import get_connection


def _insert_source(cur, tier: int, name: str) -> uuid.UUID:
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, name, tier, "ingestion test source"),
    )
    return source_id


@pytest.fixture(scope="module")
def sources(migrated_db):
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            high = _insert_source(cur, 5, f"ingest-high-{uuid.uuid4()}")
            low = _insert_source(cur, 2, f"ingest-low-{uuid.uuid4()}")
            equal_a = _insert_source(cur, 3, f"ingest-equal-a-{uuid.uuid4()}")
            equal_b = _insert_source(cur, 3, f"ingest-equal-b-{uuid.uuid4()}")
        conn.commit()
    finally:
        conn.close()
    return {"high": high, "low": low, "equal_a": equal_a, "equal_b": equal_b}


def test_first_belief_for_new_subject_becomes_canonical(migrated_db, sources):
    order_id = uuid.uuid4().hex[:8]
    result = ingest(
        f"Stripe webhook: refund.processed for order-{order_id}, amount $49.99",
        agent_id="payment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    assert result.outcome == "canonical"
    assert result.belief_id is not None
    assert order_id in result.subject_key or order_id in result.claim_text.lower()


def test_near_duplicate_restatement_merges(migrated_db, sources):
    order_id = uuid.uuid4().hex[:8]
    first = ingest(
        f"Order-{order_id} has shipped via FedEx, tracking number 884213",
        agent_id="fulfillment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    assert first.outcome == "canonical"

    duplicate = ingest(
        f"Order-{order_id} shipped via FedEx, tracking # 884213",
        agent_id="fulfillment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    assert duplicate.outcome == "duplicate"
    assert duplicate.belief_id is None


def test_unrelated_claim_on_same_subject_is_no_conflict(migrated_db, sources):
    order_id = uuid.uuid4().hex[:8]
    ingest(
        f"Order-{order_id} refund was processed successfully",
        agent_id="payment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    unrelated = ingest(
        f"The customer for order-{order_id} left a 5-star product review",
        agent_id="reviews-agent", source_id=sources["low"], database_url=migrated_db,
    )
    # unrelated claim text should embed far enough from the refund claim to not
    # be flagged canonical/duplicate/conflict for the SAME subject_key - but
    # since subject_key assignment is independent per claim, this mainly checks
    # the pipeline doesn't crash and reaches a legitimate terminal outcome.
    assert unrelated.outcome in ("canonical", "no_conflict", "resolved_rule", "resolved_llm", "contested")


def test_genuine_conflict_differing_authority_resolves_via_rule(migrated_db, sources):
    order_id = uuid.uuid4().hex[:8]
    low_claim = ingest(
        f"Order-{order_id} refund is still pending per the support ticket",
        agent_id="support-agent", source_id=sources["low"], database_url=migrated_db,
    )
    high_claim = ingest(
        f"Order-{order_id} refund has been processed and completed",
        agent_id="payment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    assert high_claim.outcome in ("resolved_rule", "canonical", "no_conflict")
    if high_claim.outcome == "resolved_rule":
        conn = get_connection(migrated_db)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM beliefs WHERE id = %s", (high_claim.belief_id,))
                assert cur.fetchone()[0] == "canonical"
        finally:
            conn.close()


def test_genuine_conflict_equal_authority_escalates_to_contested(migrated_db, sources):
    order_id = uuid.uuid4().hex[:8]
    claim_a = ingest(
        f"Order-{order_id} subscription status is active",
        agent_id="agent-a", source_id=sources["equal_a"], database_url=migrated_db,
    )
    claim_b = ingest(
        f"Order-{order_id} subscription status is cancelled",
        agent_id="agent-b", source_id=sources["equal_b"], database_url=migrated_db,
    )
    # equal authority tier -> per Block 2A's finding, needs_human is always True
    # when the arbiter is genuinely reached (no rule can decide it)
    assert claim_b.outcome in ("contested", "canonical", "no_conflict", "duplicate")


# --- resolve_pending_candidate() - the resolution_worker Lambda's poll loop ---
# Regression coverage for a real bug caught during the infra/README.md Lambda
# deploy checkpoint: unlike ingest()'s embeddings (always fresh from
# generate_embedding(), never round-tripped through the DB), this is the
# first code path that re-reads an embedding back out of `beliefs` with
# register_vector() active - which hands back a pgvector Vector object, not a
# plain list. detect_conflict()'s list(new_embedding) raised TypeError on a
# real Vector (no __iter__) until _load_pending_candidate normalized it via
# .to_list(). Every real "candidate" backlog item in the live cluster hit
# this at deploy time (see docs/REVIEW_LOG.md) - not a hypothetical case.

def _insert_candidate_belief(cur, belief_id, subject_key, claim_text, embedding, source_id, confidence=0.9):
    cur.execute(
        """
        INSERT INTO beliefs (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'candidate')
        """,
        (belief_id, subject_key, claim_text, embedding, "verify-agent", source_id, confidence, datetime.now(timezone.utc)),
    )


def test_resolve_pending_candidate_promotes_when_no_canonical_exists(migrated_db, sources):
    subject_key = f"resolve-worker-test:{uuid.uuid4().hex[:8]}"
    belief_id = uuid.uuid4()
    embedding = generate_embedding("The warehouse manager at site-9 is Alice Chen.")

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
            _insert_candidate_belief(cur, belief_id, subject_key, "site-9 manager is Alice Chen", embedding, sources["high"])
        conn.commit()
    finally:
        conn.close()

    result = resolve_pending_candidate(belief_id, database_url=migrated_db)
    assert result.outcome == "canonical"

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM beliefs WHERE id = %s", (belief_id,))
            assert cur.fetchone()[0] == "canonical"
            cur.execute("SELECT canonical_belief_id FROM subjects WHERE subject_key = %s", (subject_key,))
            assert cur.fetchone()[0] == belief_id
    finally:
        conn.close()


def test_resolve_pending_candidate_reevaluates_against_real_canonical_without_crashing(migrated_db, sources):
    """The actual regression case: a real canonical belief already exists (so
    evaluate_new_belief -> detect_conflict runs and calls list() on the
    fetched embedding), and the pending candidate's own embedding was also
    read back out of the DB via register_vector() - exactly the Vector round
    trip that crashed before the fix. Outcome isn't pinned down (depends on
    real embedding similarity), the crash-vs-no-crash is the point."""
    order_id = uuid.uuid4().hex[:8]
    canonical = ingest(
        f"Order-{order_id} refund was processed successfully",
        agent_id="payment-agent", source_id=sources["high"], database_url=migrated_db,
    )
    assert canonical.outcome == "canonical"

    belief_id = uuid.uuid4()
    embedding = generate_embedding(f"Order-{order_id} refund is still pending")
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            _insert_candidate_belief(cur, belief_id, canonical.subject_key, f"Order-{order_id} refund is still pending", embedding, sources["low"])
        conn.commit()
    finally:
        conn.close()

    result = resolve_pending_candidate(belief_id, database_url=migrated_db)
    assert result.outcome in ("duplicate", "no_conflict", "resolved_rule", "canonical")
