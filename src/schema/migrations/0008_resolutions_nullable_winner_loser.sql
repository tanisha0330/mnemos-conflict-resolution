-- Schema gap flagged in docs/REVIEW_LOG.md Known Problem #4: a genuine
-- arbiter winner="neither" verdict has no valid (winner, loser) pair to
-- store, so it previously left no resolutions row at all - only a belief
-- status flip. Made nullable, with a CHECK that they're both-null or
-- both-set, so "neither" outcomes still get a full audit-trail row
-- (verdict, reasoning, confidence, method) without fabricating a winner.
ALTER TABLE resolutions ALTER COLUMN winner_belief_id DROP NOT NULL;
ALTER TABLE resolutions ALTER COLUMN loser_belief_id DROP NOT NULL;

ALTER TABLE resolutions ADD CONSTRAINT resolutions_winner_loser_both_or_neither CHECK (
    (winner_belief_id IS NULL AND loser_belief_id IS NULL)
    OR (winner_belief_id IS NOT NULL AND loser_belief_id IS NOT NULL)
);
