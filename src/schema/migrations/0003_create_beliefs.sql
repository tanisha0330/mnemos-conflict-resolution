CREATE TYPE IF NOT EXISTS belief_status AS ENUM ('candidate', 'canonical', 'superseded', 'contested');

CREATE TABLE IF NOT EXISTS beliefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_key TEXT NOT NULL REFERENCES subjects (subject_key),
    claim_text TEXT NOT NULL,
    embedding VECTOR(1024),
    agent_id TEXT NOT NULL,
    source_id UUID NOT NULL REFERENCES sources (id),
    confidence FLOAT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    status belief_status NOT NULL DEFAULT 'candidate',
    superseded_by UUID REFERENCES beliefs (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS beliefs_subject_key_idx ON beliefs (subject_key);
CREATE INDEX IF NOT EXISTS beliefs_status_idx ON beliefs (status);
