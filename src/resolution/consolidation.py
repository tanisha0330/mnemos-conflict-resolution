"""Consolidation job: merges near-duplicate beliefs on a subject that slipped
through Stage 1 as independent 'candidate' rows (Stage 1 only compares a new
belief against the CURRENT canonical at ingest time, so two candidates that
are near-duplicates of each other - or of the canonical - can accumulate
without ever being flagged).

Interpretation note: the build spec's literal wording is "near-duplicate
canonical beliefs on the same subject", but only one belief is ever canonical
per subject at a time by design (subjects.canonical_belief_id), so two
beliefs can't both BE canonical. Implemented here as the well-defined,
useful version: sweep a subject's 'candidate' beliefs, and for each one
that's >0.9 similar to the current canonical (or to another candidate, if
there's no canonical yet), keep whichever is more complete (longer
claim_text) or more recent, and mark the other 'superseded' - promoting the
survivor to canonical if it wasn't already.
"""

from dataclasses import dataclass
from datetime import datetime

from src.resolution.detection import DUPLICATE_THRESHOLD


@dataclass(frozen=True)
class ConsolidationResult:
    subject_key: str
    merged_count: int
    promoted_new_canonical: bool


def _more_complete(a_claim_text: str, a_observed_at: datetime, b_claim_text: str, b_observed_at: datetime) -> bool:
    """True if a is judged more complete/recent than b."""
    if len(a_claim_text) != len(b_claim_text):
        return len(a_claim_text) > len(b_claim_text)
    return a_observed_at > b_observed_at


def consolidate_subject(conn, subject_key: str) -> ConsolidationResult:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_belief_id, version FROM subjects WHERE subject_key = %s",
            (subject_key,),
        )
        row = cur.fetchone()
        if row is None:
            return ConsolidationResult(subject_key, 0, False)
        canonical_id, version = row

        cur.execute(
            "SELECT id, claim_text, embedding, observed_at FROM beliefs "
            "WHERE subject_key = %s AND status = 'candidate' AND embedding IS NOT NULL",
            (subject_key,),
        )
        candidates = cur.fetchall()

        if canonical_id is not None:
            cur.execute("SELECT claim_text, observed_at FROM beliefs WHERE id = %s", (canonical_id,))
            survivor_id, (survivor_text, survivor_observed_at) = canonical_id, cur.fetchone()
        else:
            survivor_id, survivor_text, survivor_observed_at = None, None, None

        merged = 0
        promoted = False

        for candidate_id, candidate_text, candidate_embedding, candidate_observed_at in candidates:
            if survivor_id is None:
                survivor_id, survivor_text, survivor_observed_at = candidate_id, candidate_text, candidate_observed_at
                continue

            cur.execute(
                "SELECT 1 - (embedding <=> %s::vector) FROM beliefs WHERE id = %s",
                (candidate_embedding, survivor_id),
            )
            similarity = float(cur.fetchone()[0])
            if similarity <= DUPLICATE_THRESHOLD:
                continue

            if _more_complete(candidate_text, candidate_observed_at, survivor_text, survivor_observed_at):
                cur.execute(
                    "UPDATE beliefs SET status = 'superseded', superseded_by = %s WHERE id = %s",
                    (candidate_id, survivor_id),
                )
                survivor_id, survivor_text, survivor_observed_at = candidate_id, candidate_text, candidate_observed_at
                promoted = True
            else:
                cur.execute(
                    "UPDATE beliefs SET status = 'superseded', superseded_by = %s WHERE id = %s",
                    (survivor_id, candidate_id),
                )
            merged += 1

        if promoted and survivor_id != canonical_id:
            cur.execute("UPDATE beliefs SET status = 'canonical' WHERE id = %s", (survivor_id,))
            cur.execute(
                "UPDATE subjects SET canonical_belief_id = %s, version = version + 1, updated_at = now() WHERE subject_key = %s",
                (survivor_id, subject_key),
            )
    conn.commit()
    return ConsolidationResult(subject_key, merged, promoted)
