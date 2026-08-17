"""Real, non-mocked tests against the live cluster for ground-truth
verification (src.resolution.verification / src.verification.ledger) - the
same "test against reality" bar the rest of this project's DB-touching code
uses, since the whole point of this mechanism is that it queries a real
table, not a mock."""

import uuid

import pytest

from src.resolution.verification import verify_against_ledger
from src.schema.db import get_connection
from src.verification.ledger import get_refund_status, upsert_refund_status


def test_non_refund_subject_never_decides(migrated_db):
    conn = get_connection(migrated_db)
    try:
        result = verify_against_ledger(conn, "shipping_carrier:order-1", "shipped via FedEx", "shipped via UPS")
    finally:
        conn.close()
    assert result.decided is False


def test_refund_subject_with_no_ledger_record_does_not_decide(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        result = verify_against_ledger(
            conn, f"refund_status:order-{order_id}",
            "Refund is still pending", "Refund was processed",
        )
    finally:
        conn.close()
    assert result.decided is False


def test_ledger_confirms_new_claim_over_existing(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_refund_status(conn, order_id, "processed", 49.99)
        result = verify_against_ledger(
            conn, f"refund_status:order-{order_id}",
            existing_claim_text="Refund is still pending",
            new_claim_text="Refund was processed",
        )
    finally:
        conn.close()
    assert result.decided is True
    assert result.winner == "new"
    assert "processed" in result.reason
    assert order_id in result.reason


def test_ledger_confirms_existing_claim_over_new(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_refund_status(conn, order_id, "pending", 49.99)
        result = verify_against_ledger(
            conn, f"refund_status:order-{order_id}",
            existing_claim_text="Refund is still pending",
            new_claim_text="Refund was processed",
        )
    finally:
        conn.close()
    assert result.decided is True
    assert result.winner == "existing"


def test_ambiguous_ledger_value_does_not_decide(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_refund_status(conn, order_id, "refunded_partially", 20.00)  # not in the known keyword sets
        result = verify_against_ledger(
            conn, f"refund_status:order-{order_id}",
            "Refund is still pending", "Refund was processed",
        )
    finally:
        conn.close()
    assert result.decided is False


def test_upsert_is_idempotent_and_updates_status(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_refund_status(conn, order_id, "pending", 10.0)
        assert get_refund_status(conn, order_id) == "pending"
        upsert_refund_status(conn, order_id, "processed", 10.0)
        assert get_refund_status(conn, order_id) == "processed"
    finally:
        conn.close()
