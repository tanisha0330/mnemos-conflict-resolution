"""Demo CLI: seeds the canonical refund-conflict scenario from CLAUDE.md
(payment-agent reads a mock Stripe API, support-agent reads a mock Zendesk
ticket, they disagree, fulfillment-agent reads memory to decide), resolves
it through the real ingestion pipeline, and prints an as_of() result
readably. This is the backbone of the live demo (Block 4B).

Usage: python -m scripts.demo_cli [DATABASE_URL]
"""

import sys
import uuid
from datetime import datetime, timezone

# Windows terminals often default to a legacy codepage (cp1252) that can't
# encode characters like the em dash used in as_of()'s pretty-printed output.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from src.api.client import MnemosClient
from src.schema.db import get_connection


def seed_demo_sources(database_url=None):
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            stripe_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (stripe_id, f"demo-stripe-{uuid.uuid4().hex[:6]}", 5, "Payment processor API - system of record"),
            )
            zendesk_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (zendesk_id, f"demo-zendesk-{uuid.uuid4().hex[:6]}", 3, "Support ticket text, human-entered"),
            )
        conn.commit()
    finally:
        conn.close()
    return stripe_id, zendesk_id


def run_demo(database_url=None):
    order_id = f"demo{uuid.uuid4().hex[:8]}"
    stripe_id, zendesk_id = seed_demo_sources(database_url)
    client = MnemosClient(database_url=database_url)

    print(f"Simulating order {order_id}\n")

    print("1. support-agent reads a Zendesk support ticket...")
    support_result = client.add(
        f"Zendesk ticket: customer says refund for order-{order_id} is still pending",
        agent_id="support-agent", source_id=zendesk_id,
    )
    print(f"   ingested ({support_result.outcome}): \"{support_result.claim_text}\"\n")

    print("2. payment-agent reads the Stripe API...")
    payment_result = client.add(
        f"Stripe webhook: refund.processed for order-{order_id}",
        agent_id="payment-agent", source_id=stripe_id,
    )
    print(f"   ingested ({payment_result.outcome}): \"{payment_result.claim_text}\"")
    if payment_result.detail:
        print(f"   resolution reasoning: {payment_result.detail}")
    print()

    subject_key = payment_result.subject_key
    print(f"3. fulfillment-agent checks memory for subject '{subject_key}'...")
    current = client.search("refund status", subject_key=subject_key)
    if current:
        print(f"   canonical answer: \"{current[0].claim_text}\" (source={current[0].source_name})")
        print("   -> fulfillment-agent correctly does NOT issue a duplicate refund.\n")
    else:
        print("   no canonical answer yet - likely escalated to a human (contested).\n")

    print("=" * 64)
    print("4. Time-travel: what did we believe right now, and why?")
    print("=" * 64)
    result = client.as_of(subject_key, datetime.now(timezone.utc))
    print(result.pretty())


if __name__ == "__main__":
    database_url = sys.argv[1] if len(sys.argv) > 1 else None
    run_demo(database_url)
