"""Real, non-mocked tests against the live cluster for the second built-in
verifier (src.verification.shipment_ledger) - proving
src.resolution.verification's registry generalizes to a different
attribute/backing-table pair, not just the original refund_status one.
Mirrors tests/test_verification.py's structure and "test against reality"
bar."""

import uuid

import pytest

from src.resolution.verification import verify_against_ledger
from src.schema.db import get_connection
from src.verification.shipment_ledger import get_shipment_carrier, upsert_shipment_carrier


def test_non_shipping_subject_never_decides(migrated_db):
    conn = get_connection(migrated_db)
    try:
        result = verify_against_ledger(conn, "refund_status:order-1", "refund pending", "refund processed")
    finally:
        conn.close()
    assert result.decided is False


def test_shipping_subject_with_no_ledger_record_does_not_decide(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        result = verify_against_ledger(
            conn, f"shipping_carrier:order-{order_id}",
            "shipped via FedEx", "shipped via UPS",
        )
    finally:
        conn.close()
    assert result.decided is False


def test_ledger_confirms_new_claim_over_existing(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_shipment_carrier(conn, order_id, "fedex")
        result = verify_against_ledger(
            conn, f"shipping_carrier:order-{order_id}",
            existing_claim_text="shipped via UPS",
            new_claim_text="shipped via FedEx",
        )
    finally:
        conn.close()
    assert result.decided is True
    assert result.winner == "new"
    assert "fedex" in result.reason


def test_ledger_confirms_existing_claim_over_new(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_shipment_carrier(conn, order_id, "ups")
        result = verify_against_ledger(
            conn, f"shipping_carrier:order-{order_id}",
            existing_claim_text="shipped via UPS",
            new_claim_text="shipped via FedEx",
        )
    finally:
        conn.close()
    assert result.decided is True
    assert result.winner == "existing"


def test_ambiguous_ledger_value_does_not_decide(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_shipment_carrier(conn, order_id, "ontrac")  # not in the known keyword sets
        result = verify_against_ledger(
            conn, f"shipping_carrier:order-{order_id}",
            "shipped via UPS", "shipped via FedEx",
        )
    finally:
        conn.close()
    assert result.decided is False


def test_upsert_is_idempotent_and_updates_carrier(migrated_db):
    order_id = uuid.uuid4().hex[:8]
    conn = get_connection(migrated_db)
    try:
        upsert_shipment_carrier(conn, order_id, "ups")
        assert get_shipment_carrier(conn, order_id) == "ups"
        upsert_shipment_carrier(conn, order_id, "fedex")
        assert get_shipment_carrier(conn, order_id) == "fedex"
    finally:
        conn.close()
