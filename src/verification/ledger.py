"""A real system-of-record table (`payment_ledger`), independent of any
agent's claim, standing in for what a production system would query live
(the actual Stripe/payment-processor balance API) rather than trusting a
secondhand claim about it. This is the mechanism behind
src.resolution.verification: for a verifiable-transaction subject, the
resolution pipeline can check the actual current state directly instead of
only weighing two claims against each other via authority/recency/confidence
heuristics.
"""

from decimal import Decimal

from src.resolution.verification import VerificationResult

_STATUS_KEYWORDS = {
    "processed": ["processed", "completed", "was refunded", "refund issued", "refunded successfully"],
    "pending": ["pending", "still pending", "not yet processed", "awaiting", "in progress",
                "hasn't been processed", "has not been processed"],
}


def _matches_status(claim_text: str, status: str) -> bool:
    text_lower = claim_text.lower()
    return any(kw in text_lower for kw in _STATUS_KEYWORDS.get(status, []))


def verify_refund_status(conn, order_id: str, existing_claim_text: str, new_claim_text: str) -> VerificationResult:
    """Verifier registered for the "refund_status" attribute - see
    src.resolution.verification.register_verifier. Matches the Verifier
    signature: (conn, entity_id, existing_claim_text, new_claim_text)."""
    ledger_status = get_refund_status(conn, order_id)
    if ledger_status is None:
        return VerificationResult(decided=False)

    existing_matches = _matches_status(existing_claim_text, ledger_status)
    new_matches = _matches_status(new_claim_text, ledger_status)

    if existing_matches and not new_matches:
        return VerificationResult(
            decided=True, winner="existing",
            reason=(
                f"payment ledger (real system-of-record, independent of either claim) shows "
                f"refund_status={ledger_status!r} for order-{order_id}. This confirms the existing "
                f"claim ({existing_claim_text!r}) and contradicts the new claim ({new_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    if new_matches and not existing_matches:
        return VerificationResult(
            decided=True, winner="new",
            reason=(
                f"payment ledger (real system-of-record, independent of either claim) shows "
                f"refund_status={ledger_status!r} for order-{order_id}. This confirms the new "
                f"claim ({new_claim_text!r}) and contradicts the existing claim ({existing_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    return VerificationResult(decided=False)


def upsert_refund_status(conn, order_id: str, refund_status: str, amount: Decimal | float | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payment_ledger (order_id, refund_status, amount, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (order_id) DO UPDATE SET
                refund_status = excluded.refund_status,
                amount = excluded.amount,
                updated_at = now()
            """,
            (order_id, refund_status, amount),
        )
    conn.commit()


def get_refund_status(conn, order_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT refund_status FROM payment_ledger WHERE order_id = %s", (order_id,))
        row = cur.fetchone()
    return row[0] if row else None
