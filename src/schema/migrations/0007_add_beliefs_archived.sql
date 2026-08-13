-- Block 3C decay policy needs a way to deprioritize old superseded beliefs
-- from default queries without ever deleting them ("claims not facts").
-- Sanctioned schema evolution: docs/mnemos-build-sequence.md's Block 3C text
-- explicitly requires this column; not an undirected redesign of the Block 1A
-- schema. Logged in docs/REVIEW_LOG.md for visibility regardless.
ALTER TABLE beliefs ADD COLUMN IF NOT EXISTS archived BOOL NOT NULL DEFAULT false;
