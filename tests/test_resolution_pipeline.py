import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.resolution.pipeline import PipelineOutcome, evaluate_new_belief
from src.resolution.rules import RuleCandidate, RuleOutcome
from src.schema.db import get_connection

NOW = datetime.now(timezone.utc)


def _blended_embedding(base: np.ndarray, orthogonal: np.ndarray, similarity: float) -> list[float]:
    """Constructs a unit vector with cosine similarity == `similarity` to `base`,
    given `base` and `orthogonal` are already orthonormal."""
    vec = similarity * base + np.sqrt(1 - similarity**2) * orthogonal
    return (vec / np.linalg.norm(vec)).tolist()


def _orthonormal_pair(seed: int):
    rng = np.random.default_rng(seed)
    a = rng.standard_normal(1024)
    a /= np.linalg.norm(a)
    b = rng.standard_normal(1024)
    b -= (b @ a) * a  # remove the component along a
    b /= np.linalg.norm(b)
    return a, b


def _insert_source(cur, authority_tier: int) -> uuid.UUID:
    source_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
        (source_id, f"src-{source_id}", authority_tier, "test source"),
    )
    return source_id


def _insert_subject(cur, subject_key: str, volatility: str):
    cur.execute(
        "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
        "VALUES (%s, NULL, 1, %s, now())",
        (subject_key, volatility),
    )


def _insert_canonical_belief(cur, subject_key: str, source_id, embedding, confidence, observed_at) -> uuid.UUID:
    belief_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO beliefs
            (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, 'canonical claim', %s, 'test-agent', %s, %s, %s, 'canonical')
        """,
        (belief_id, subject_key, embedding, source_id, confidence, observed_at),
    )
    cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (belief_id, subject_key))
    return belief_id


def test_no_canonical(migrated_db):
    subject_key = f"test:pipeline:no-canonical:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            _insert_subject(cur, subject_key, "stable")
        conn.commit()

        base, _ = _orthonormal_pair(10)
        result = evaluate_new_belief(
            conn, subject_key, base.tolist(),
            RuleCandidate(authority_tier=3, confidence=0.8, observed_at=NOW), "stable", NOW,
        )
        assert result.outcome == PipelineOutcome.NO_CANONICAL
    finally:
        conn.close()


def test_duplicate(migrated_db):
    subject_key = f"test:pipeline:dup:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        base, _ = _orthonormal_pair(11)
        with conn.cursor() as cur:
            source_id = _insert_source(cur, 3)
            _insert_subject(cur, subject_key, "stable")
            _insert_canonical_belief(cur, subject_key, source_id, base.tolist(), 0.9, NOW)
        conn.commit()

        result = evaluate_new_belief(
            conn, subject_key, base.tolist(),
            RuleCandidate(authority_tier=3, confidence=0.8, observed_at=NOW), "stable", NOW,
        )
        assert result.outcome == PipelineOutcome.DUPLICATE
    finally:
        conn.close()


def test_no_conflict(migrated_db):
    subject_key = f"test:pipeline:no-conflict:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        base, orth = _orthonormal_pair(12)
        with conn.cursor() as cur:
            source_id = _insert_source(cur, 3)
            _insert_subject(cur, subject_key, "stable")
            _insert_canonical_belief(cur, subject_key, source_id, base.tolist(), 0.9, NOW)
        conn.commit()

        result = evaluate_new_belief(
            conn, subject_key, orth.tolist(),  # exactly orthogonal -> similarity 0.0
            RuleCandidate(authority_tier=3, confidence=0.8, observed_at=NOW), "stable", NOW,
        )
        assert result.outcome == PipelineOutcome.NO_CONFLICT
    finally:
        conn.close()


def test_rule_decided_by_authority_tier(migrated_db):
    subject_key = f"test:pipeline:rule-decided:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        base, orth = _orthonormal_pair(13)
        conflicting_embedding = _blended_embedding(base, orth, 0.7)
        with conn.cursor() as cur:
            source_id = _insert_source(cur, 2)  # low authority
            _insert_subject(cur, subject_key, "stable")
            _insert_canonical_belief(cur, subject_key, source_id, base.tolist(), 0.9, NOW)
        conn.commit()

        result = evaluate_new_belief(
            conn, subject_key, conflicting_embedding,
            RuleCandidate(authority_tier=5, confidence=0.8, observed_at=NOW), "stable", NOW,  # higher authority
        )
        assert result.outcome == PipelineOutcome.RULE_DECIDED
        assert result.rule_outcome == RuleOutcome.NEW_WINS
        assert result.rule_name == "authority_tier"
        assert 0.5 <= result.similarity <= 0.9
    finally:
        conn.close()


def test_needs_llm_when_no_rule_decides(migrated_db):
    subject_key = f"test:pipeline:needs-llm:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        base, orth = _orthonormal_pair(14)
        conflicting_embedding = _blended_embedding(base, orth, 0.7)
        with conn.cursor() as cur:
            source_id = _insert_source(cur, 3)
            _insert_subject(cur, subject_key, "stable")
            _insert_canonical_belief(cur, subject_key, source_id, base.tolist(), 0.8, NOW)
        conn.commit()

        result = evaluate_new_belief(
            conn, subject_key, conflicting_embedding,
            RuleCandidate(authority_tier=3, confidence=0.75, observed_at=NOW),  # same tier, both above floor, stable
            "stable", NOW,
        )
        assert result.outcome == PipelineOutcome.NEEDS_LLM
    finally:
        conn.close()


def test_real_seed_conflict_pair_resolves_via_authority_tier(seeded_db):
    """Re-runs the actual seeded order-12345 conflict pair's field values through
    the rules engine directly, confirming the deterministic outcome matches what
    was hand-seeded (stripe_api tier 5 beats zendesk_tickets tier 3)."""
    conn = get_connection(seeded_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.name, s.authority_tier, b.confidence, b.observed_at
                FROM beliefs b JOIN sources s ON s.id = b.source_id
                WHERE b.subject_key = 'refund_status:order-12345' AND s.name IN ('stripe_api', 'zendesk_tickets')
                ORDER BY s.name
                """
            )
            rows = {r[0]: RuleCandidate(authority_tier=r[1], confidence=float(r[2]), observed_at=r[3]) for r in cur.fetchall()}

        from src.resolution.rules import apply_rules

        result = apply_rules(rows["zendesk_tickets"], rows["stripe_api"], "volatile", datetime.now(timezone.utc))
        assert result.outcome == RuleOutcome.NEW_WINS
        assert result.rule == "authority_tier"
    finally:
        conn.close()
