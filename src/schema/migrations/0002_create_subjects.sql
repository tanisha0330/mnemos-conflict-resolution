CREATE TYPE IF NOT EXISTS subject_volatility AS ENUM ('stable', 'volatile');

-- canonical_belief_id has no FK yet: beliefs doesn't exist until migration 0003.
-- The FK is added in 0004 once both tables exist (subjects <-> beliefs is a cycle).
CREATE TABLE IF NOT EXISTS subjects (
    subject_key TEXT PRIMARY KEY,
    canonical_belief_id UUID,
    version INT NOT NULL DEFAULT 1,
    volatility subject_volatility NOT NULL DEFAULT 'stable',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
