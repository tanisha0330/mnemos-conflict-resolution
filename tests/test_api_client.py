"""Real, non-mocked integration tests for src/api/client.py against the live
cluster - including the seeded order-12345 conflict pair (the Block 3B
checkpoint's own example)."""

import time
import uuid
from datetime import datetime, timezone

import pytest

from src.api.client import MnemosClient
from src.schema.db import get_connection


@pytest.fixture(scope="module")
def client(migrated_db):
    return MnemosClient(database_url=migrated_db)


def test_search_canonical_only_excludes_superseded(client, seeded_db):
    results = client.search("refund status for order-12345", subject_key="refund_status:order-12345")
    assert len(results) >= 1
    assert all(b.status == "canonical" for b in results)
    assert all(b.source_name != "zendesk_tickets" for b in results)  # the superseded loser


def test_search_include_superseded_returns_more(client, seeded_db):
    canonical_only = client.search("refund status for order-12345", subject_key="refund_status:order-12345")
    with_superseded = client.search(
        "refund status for order-12345", subject_key="refund_status:order-12345", include_superseded=True
    )
    assert len(with_superseded) >= len(canonical_only)
    statuses = {b.status for b in with_superseded}
    assert "superseded" in statuses


def test_get_all_canonical_only_by_default(client, seeded_db):
    results = client.get_all("refund_status:order-12345")
    assert all(b.status == "canonical" for b in results)
    assert len(results) == 1


def test_get_all_include_superseded(client, seeded_db):
    results = client.get_all("refund_status:order-12345", include_superseded=True)
    statuses = {b.status for b in results}
    assert "canonical" in statuses and "superseded" in statuses
    assert len(results) == 4  # matches the 4 seeded beliefs for this subject


def test_history_includes_resolution_reasoning(client, seeded_db):
    entries = client.history("refund_status:order-12345")
    assert len(entries) == 4

    zendesk_entry = next(e for e in entries if e.belief.source_name == "zendesk_tickets")
    assert zendesk_entry.belief.status == "superseded"
    assert zendesk_entry.resolution is not None
    assert zendesk_entry.resolution.verdict == "contradiction"
    assert "authority_tier" in zendesk_entry.resolution.reasoning

    stripe_canonical = next(e for e in entries if e.belief.status == "canonical")
    assert stripe_canonical.resolution is not None
    assert stripe_canonical.resolution.winner_belief_id == stripe_canonical.belief.id


def test_as_of_reflects_state_at_a_past_timestamp(client, migrated_db):
    subject_key = f"test:asof:{uuid.uuid4()}"
    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            source_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (source_id, f"asof-src-{uuid.uuid4()}", 3, "test"),
            )
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
            belief_1 = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO beliefs (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
                VALUES (%s, %s, 'first claim', 'test-agent', %s, 0.8, now(), 'canonical')
                """,
                (belief_1, subject_key, source_id),
            )
            cur.execute("UPDATE subjects SET canonical_belief_id = %s WHERE subject_key = %s", (belief_1, subject_key))
        conn.commit()
    finally:
        conn.close()

    time.sleep(2)
    midpoint = datetime.now(timezone.utc)
    time.sleep(2)

    conn = get_connection(migrated_db)
    try:
        with conn.cursor() as cur:
            belief_2 = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO beliefs (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
                VALUES (%s, %s, 'second claim, replaces first', 'test-agent', %s, 0.9, now(), 'canonical')
                """,
                (belief_2, subject_key, source_id),
            )
            cur.execute(
                "UPDATE subjects SET canonical_belief_id = %s, version = version + 1 WHERE subject_key = %s",
                (belief_2, subject_key),
            )
        conn.commit()
    finally:
        conn.close()

    client_local = MnemosClient(database_url=migrated_db)
    past_state = client_local.as_of(subject_key, midpoint)
    current_state = client_local.as_of(subject_key, datetime.now(timezone.utc))

    assert past_state.canonical.id == belief_1
    assert current_state.canonical.id == belief_2
    assert isinstance(past_state.pretty(), str) and subject_key in past_state.pretty()
