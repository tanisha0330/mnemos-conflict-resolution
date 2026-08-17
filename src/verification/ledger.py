"""A real system-of-record table (`payment_ledger`), independent of any
agent's claim, standing in for what a production system would query live
(the actual Stripe/payment-processor balance API) rather than trusting a
secondhand claim about it. This is the mechanism behind
src.resolution.verification: for a verifiable-transaction subject, the
resolution pipeline can check the actual current state directly instead of
only weighing two claims against each other via authority/recency/confidence
heuristics.
"""

import uuid
from decimal import Decimal


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
