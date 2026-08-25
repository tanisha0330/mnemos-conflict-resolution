"""A second, independent system-of-record (`shipment_ledger`), standing in
for a real courier/tracking API, for the "shipping_carrier" attribute. This
exists to prove src.resolution.verification's registry generalizes beyond
the original refund_status verifier (src.verification.ledger) - a different
attribute, a different backing table, registered the same way, no changes
to the dispatch logic.
"""

from src.resolution.verification import VerificationResult

_CARRIER_KEYWORDS = {
    "fedex": ["fedex"],
    "ups": ["ups"],
    "usps": ["usps"],
    "dhl": ["dhl"],
}


def _matches_carrier(claim_text: str, carrier: str) -> bool:
    text_lower = claim_text.lower()
    return any(kw in text_lower for kw in _CARRIER_KEYWORDS.get(carrier, [carrier]))


def upsert_shipment_carrier(conn, order_id: str, carrier: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shipment_ledger (order_id, carrier, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (order_id) DO UPDATE SET
                carrier = excluded.carrier,
                updated_at = now()
            """,
            (order_id, carrier),
        )
    conn.commit()


def get_shipment_carrier(conn, order_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT carrier FROM shipment_ledger WHERE order_id = %s", (order_id,))
        row = cur.fetchone()
    return row[0] if row else None


def verify_shipping_carrier(conn, order_id: str, existing_claim_text: str, new_claim_text: str) -> VerificationResult:
    """Verifier registered for the "shipping_carrier" attribute - see
    src.resolution.verification.register_verifier. Matches the Verifier
    signature: (conn, entity_id, existing_claim_text, new_claim_text)."""
    carrier = get_shipment_carrier(conn, order_id)
    if carrier is None:
        return VerificationResult(decided=False)

    existing_matches = _matches_carrier(existing_claim_text, carrier)
    new_matches = _matches_carrier(new_claim_text, carrier)

    if existing_matches and not new_matches:
        return VerificationResult(
            decided=True, winner="existing",
            reason=(
                f"shipment ledger (real system-of-record, independent of either claim) shows "
                f"carrier={carrier!r} for order-{order_id}. This confirms the existing claim "
                f"({existing_claim_text!r}) and contradicts the new claim ({new_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    if new_matches and not existing_matches:
        return VerificationResult(
            decided=True, winner="new",
            reason=(
                f"shipment ledger (real system-of-record, independent of either claim) shows "
                f"carrier={carrier!r} for order-{order_id}. This confirms the new claim "
                f"({new_claim_text!r}) and contradicts the existing claim ({existing_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    return VerificationResult(decided=False)
