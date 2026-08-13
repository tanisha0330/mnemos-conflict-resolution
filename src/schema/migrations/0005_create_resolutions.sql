CREATE TYPE IF NOT EXISTS resolution_verdict AS ENUM ('contradiction', 'refinement', 'temporal_shift', 'both_valid');
CREATE TYPE IF NOT EXISTS resolution_method AS ENUM ('rule', 'llm');

CREATE TABLE IF NOT EXISTS resolutions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_key TEXT NOT NULL REFERENCES subjects (subject_key),
    winner_belief_id UUID NOT NULL REFERENCES beliefs (id),
    loser_belief_id UUID NOT NULL REFERENCES beliefs (id),
    verdict resolution_verdict NOT NULL,
    reasoning TEXT NOT NULL,
    method resolution_method NOT NULL,
    confidence FLOAT NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS resolutions_subject_key_idx ON resolutions (subject_key);
