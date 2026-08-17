"""Seeds sample data: 5 sources spanning different authority tiers, 3 subjects,
and 10 beliefs including one deliberate conflict pair (mirrors the hackathon
demo scenario: a payment API and a support ticket disagree about refund status).

Embeddings are deterministic synthetic vectors (seeded RNG, L2-normalized) so
seeding and the tests that depend on it don't require live Bedrock calls.

Usage: python -m src.schema.seed [DATABASE_URL]
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from src.schema.db import get_connection

EMBEDDING_DIM = 1024


def _synthetic_embedding(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(EMBEDDING_DIM)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def seed(database_url: str | None = None) -> dict:
    """Inserts seed data. Safe to call once against an empty schema; raises on
    conflict (e.g. duplicate source names) if run twice against the same data."""
    now = datetime.now(timezone.utc)

    source_ids = {name: uuid.uuid4() for name in [
        "stripe_api", "internal_db", "zendesk_tickets", "agent_inference", "public_web",
    ]}
    sources = [
        (source_ids["stripe_api"], "stripe_api", 5, "Payment processor API - system of record for transaction state"),
        (source_ids["internal_db"], "internal_db", 4, "Internal transactional database records"),
        (source_ids["zendesk_tickets"], "zendesk_tickets", 3, "Customer support ticket text, human-entered"),
        (source_ids["agent_inference"], "agent_inference", 2, "LLM agent inference/summary, unverified"),
        (source_ids["public_web"], "public_web", 1, "Public web scrape, unverified"),
    ]

    subject_keys = [
        "refund_status:order-12345",
        "user_email:user-789",
        "shipping_carrier:order-98765",
    ]

    belief_ids = {i: uuid.uuid4() for i in range(10)}
    beliefs = [
        # subject 1: refund_status:order-12345 (volatile) - includes the deliberate conflict pair
        dict(
            i=0, id=belief_ids[0], subject_key=subject_keys[0],
            claim_text="Refund for order-12345 has been processed and completed",
            agent_id="payment-agent", source_id=source_ids["stripe_api"],
            confidence=0.98, observed_at=now - timedelta(hours=1),
            status="canonical", superseded_by=None,
        ),
        dict(
            i=1, id=belief_ids[1], subject_key=subject_keys[0],
            claim_text="Refund for order-12345 is still pending per support ticket #4521",
            agent_id="support-agent", source_id=source_ids["zendesk_tickets"],
            confidence=0.75, observed_at=now - timedelta(hours=3),
            status="superseded", superseded_by=belief_ids[0],
        ),
        dict(
            i=2, id=belief_ids[2], subject_key=subject_keys[0],
            claim_text="Order-12345 refund amount recorded as $49.99",
            agent_id="fulfillment-agent", source_id=source_ids["internal_db"],
            confidence=0.9, observed_at=now - timedelta(hours=1),
            status="candidate", superseded_by=None,
        ),
        dict(
            i=3, id=belief_ids[3], subject_key=subject_keys[0],
            claim_text="Refund for order-12345 initiated on the payment processor side",
            agent_id="payment-agent", source_id=source_ids["stripe_api"],
            confidence=0.99, observed_at=now - timedelta(hours=2),
            status="candidate", superseded_by=None,
        ),
        # subject 2: user_email:user-789 (stable)
        dict(
            i=4, id=belief_ids[4], subject_key=subject_keys[1],
            claim_text="user-789's email is jane.doe@example.com",
            agent_id="crm-agent", source_id=source_ids["internal_db"],
            confidence=0.95, observed_at=now - timedelta(days=30),
            status="canonical", superseded_by=None,
        ),
        dict(
            i=5, id=belief_ids[5], subject_key=subject_keys[1],
            claim_text="user-789's email might be j.doe@example.com (scraped from a forum post)",
            agent_id="web-agent", source_id=source_ids["public_web"],
            confidence=0.4, observed_at=now - timedelta(days=10),
            status="contested", superseded_by=None,
        ),
        dict(
            i=6, id=belief_ids[6], subject_key=subject_keys[1],
            claim_text="user-789 confirmed their email as jane.doe@example.com in ticket #3390",
            agent_id="support-agent", source_id=source_ids["zendesk_tickets"],
            confidence=0.85, observed_at=now - timedelta(days=5),
            status="candidate", superseded_by=None,
        ),
        # subject 3: shipping_carrier:order-98765 (volatile)
        dict(
            i=7, id=belief_ids[7], subject_key=subject_keys[2],
            claim_text="order-98765 is shipped via FedEx, tracking 794658312",
            agent_id="fulfillment-agent", source_id=source_ids["internal_db"],
            confidence=0.9, observed_at=now - timedelta(hours=6),
            status="canonical", superseded_by=None,
        ),
        dict(
            i=8, id=belief_ids[8], subject_key=subject_keys[2],
            claim_text="order-98765 likely shipped via UPS based on delivery pattern",
            agent_id="logistics-agent", source_id=source_ids["agent_inference"],
            confidence=0.3, observed_at=now - timedelta(hours=8),
            status="candidate", superseded_by=None,
        ),
        dict(
            i=9, id=belief_ids[9], subject_key=subject_keys[2],
            claim_text="Customer says order-98765 arrived via FedEx",
            agent_id="support-agent", source_id=source_ids["zendesk_tickets"],
            confidence=0.7, observed_at=now - timedelta(hours=4),
            status="candidate", superseded_by=None,
        ),
    ]

    subjects = [
        (subject_keys[0], belief_ids[0], 2, "volatile", now - timedelta(hours=1)),
        (subject_keys[1], belief_ids[4], 1, "stable", now - timedelta(days=30)),
        (subject_keys[2], belief_ids[7], 1, "volatile", now - timedelta(hours=6)),
    ]

    resolution = dict(
        id=uuid.uuid4(), subject_key=subject_keys[0],
        winner_belief_id=belief_ids[0], loser_belief_id=belief_ids[1],
        verdict="contradiction",
        reasoning=(
            "stripe_api (authority_tier=5) is the system of record for payment/refund "
            "state; zendesk_tickets (authority_tier=2) is human-entered ticket text that "
            "predates the processor's confirmation. Higher-authority source wins."
        ),
        method="rule", confidence=0.95, resolved_at=now - timedelta(minutes=30),
    )

    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                sources,
            )
            # subjects inserted with canonical_belief_id NULL first: beliefs must
            # exist before the FK-checked UPDATE that points to them.
            cur.executemany(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, %s, %s, %s)",
                [(s[0], s[2], s[3], s[4]) for s in subjects],
            )
            cur.executemany(
                """
                INSERT INTO beliefs
                    (id, subject_key, claim_text, embedding, agent_id, source_id,
                     confidence, observed_at, status, superseded_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        b["id"], b["subject_key"], b["claim_text"], _synthetic_embedding(b["i"]),
                        b["agent_id"], b["source_id"], b["confidence"], b["observed_at"],
                        b["status"], b["superseded_by"], b["observed_at"],
                    )
                    for b in beliefs
                ],
            )
            cur.executemany(
                "UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s",
                [(s[1], s[0]) for s in subjects],
            )
            cur.execute(
                """
                INSERT INTO resolutions
                    (id, subject_key, winner_belief_id, loser_belief_id, verdict,
                     reasoning, method, confidence, resolved_at)
                VALUES (%(id)s, %(subject_key)s, %(winner_belief_id)s, %(loser_belief_id)s,
                        %(verdict)s, %(reasoning)s, %(method)s, %(confidence)s, %(resolved_at)s)
                """,
                resolution,
            )
            # Real ground-truth ledger record for order-12345 (see
            # src/resolution/verification.py) - confirms the same outcome the
            # authority_tier rule already reached, so a fresh conflict on this
            # order would now resolve via ledger verification instead.
            cur.execute(
                "INSERT INTO payment_ledger (order_id, refund_status, amount, updated_at) VALUES (%s, %s, %s, %s)",
                ("12345", "processed", 49.99, now - timedelta(minutes=30)),
            )
        conn.commit()
        return {"sources": len(sources), "subjects": len(subjects), "beliefs": len(beliefs), "resolutions": 1}
    finally:
        conn.close()


if __name__ == "__main__":
    database_url = sys.argv[1] if len(sys.argv) > 1 else None
    counts = seed(database_url)
    print(f"Seeded: {counts}")
