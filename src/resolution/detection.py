"""Stage 1 of conflict resolution: cosine-similarity conflict detection.

Compares a candidate belief's embedding against the subject's current canonical
belief. Pure classification logic is separated from the DB query so boundary
behavior is unit-testable without a live connection.
"""

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

DUPLICATE_THRESHOLD = 0.9
NO_CONFLICT_THRESHOLD = 0.4


class DetectionOutcome(str, Enum):
    NO_CANONICAL = "no_canonical"  # subject has no canonical belief yet; nothing to compare against
    DUPLICATE = "duplicate"        # similarity > 0.9: refresh observed_at on the existing belief, discard new
    NO_CONFLICT = "no_conflict"    # similarity < 0.5: unrelated, insert as an independent new candidate
    CONFLICT = "conflict"          # 0.5 <= similarity <= 0.9: real conflict, escalate to Stage 2 rules


@dataclass(frozen=True)
class DetectionResult:
    outcome: DetectionOutcome
    canonical_belief_id: UUID | None
    similarity: float | None


def classify_similarity(similarity: float) -> DetectionOutcome:
    """>0.9 duplicate, <0.4 no conflict, [0.4, 0.9] (inclusive both ends) is a real conflict."""
    if similarity > DUPLICATE_THRESHOLD:
        return DetectionOutcome.DUPLICATE
    if similarity < NO_CONFLICT_THRESHOLD:
        return DetectionOutcome.NO_CONFLICT
    return DetectionOutcome.CONFLICT


def detect_conflict(conn, subject_key: str, new_embedding) -> DetectionResult:
    """Stage 1: cosine similarity (1 - cosine distance) between new_embedding and the
    subject's current canonical belief, using <=> with an explicit ::vector cast
    (per src/schema/db.py convention: CockroachDB can't resolve <=> against an
    untyped parameter array)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.id, 1 - (b.embedding <=> %s::vector) AS similarity
            FROM subjects s
            JOIN beliefs b ON b.id = s.canonical_belief_id
            WHERE s.subject_key = %s
            """,
            (list(new_embedding), subject_key),
        )
        row = cur.fetchone()

    if row is None:
        return DetectionResult(DetectionOutcome.NO_CANONICAL, None, None)

    canonical_id, similarity = row
    similarity = float(similarity)
    return DetectionResult(classify_similarity(similarity), canonical_id, similarity)
