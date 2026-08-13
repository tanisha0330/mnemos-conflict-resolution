ALTER TABLE subjects
    ADD CONSTRAINT IF NOT EXISTS subjects_canonical_belief_fk
    FOREIGN KEY (canonical_belief_id) REFERENCES beliefs (id);
