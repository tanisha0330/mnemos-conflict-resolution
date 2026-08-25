-- Second ground-truth verifier, proving src/resolution/verification.py's
-- registry isn't a one-off special case for refunds: a real courier/shipment
-- system-of-record, independent of what any agent has claimed, for the
-- "shipping_carrier" attribute. See src/verification/shipment_ledger.py.

CREATE TABLE IF NOT EXISTS shipment_ledger (
    order_id TEXT PRIMARY KEY,
    carrier TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
