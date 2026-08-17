-- Ground-truth verification: a real system-of-record table (a payment
-- ledger), independent of what any agent has claimed, that the resolution
-- pipeline can consult directly for verifiable-transaction subjects instead
-- of only weighing secondhand claims against each other via heuristics
-- (authority tier / recency / confidence). See src/resolution/verification.py.

ALTER TYPE resolution_method ADD VALUE IF NOT EXISTS 'ledger_verification';

CREATE TABLE IF NOT EXISTS payment_ledger (
    order_id TEXT PRIMARY KEY,
    refund_status TEXT NOT NULL,
    amount DECIMAL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
