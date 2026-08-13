"""Transactional commit step for a resolution decision. Takes only plain,
pre-computed decision values (never an ArbiterInput or a call to arbitrate())
so the "no network/LLM calls inside the transaction" boundary is structurally
true, not just something to eyeball in the logic - this module doesn't import
arbiter.py at all.

Two paths:
  - commit_resolution(): the confirmed path (needs_human=False) - the 5-step
    serializable transaction from CLAUDE.md, with optimistic version checks
    and explicit retry on SQLSTATE 40001.
  - commit_contested(): the escalation path (needs_human=True) - marks both
    conflicting beliefs 'contested' without promoting either to canonical.
    See the docstring on commit_contested for a real schema gap this exposed
    (resolutions.winner_belief_id/loser_belief_id are NOT NULL, which can't
    represent an arbiter "neither" outcome) - flagged in docs/REVIEW_LOG.md,
    not silently worked around.
"""

import random
import time
import uuid
from dataclasses import dataclass
from uuid import UUID

import psycopg2.errors

from src.schema.db import get_connection

MAX_RETRIES = 10
BASE_BACKOFF_SECONDS = 0.05
MAX_BACKOFF_SECONDS = 2.0


class StaleResolutionError(Exception):
    """subjects.version changed since the decision was made (read happened
    before the arbiter/rules call, outside this transaction) - the caller
    must re-run detection/rules/arbiter against fresh state, not just retry
    with the same decision. Distinct from a raw 40001: this means the data
    the decision was based on is no longer current, not that the write
    merely collided at the storage layer."""

    def __init__(self, subject_key: str, expected_version: int):
        super().__init__(
            f"subject {subject_key!r} version changed since the decision was made "
            f"(expected version {expected_version}); re-evaluate before retrying"
        )
        self.subject_key = subject_key
        self.expected_version = expected_version


@dataclass(frozen=True)
class CommitResult:
    resolution_id: UUID | None
    new_version: int | None
    retries: int


def _backoff_sleep(attempt: int) -> None:
    delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    time.sleep(delay + random.uniform(0, delay * 0.1))


def commit_resolution(
    database_url: str | None,
    subject_key: str,
    expected_version: int,
    winner_belief_id: UUID,
    loser_belief_id: UUID,
    verdict: str,
    reasoning: str,
    method: str,
    confidence: float,
    max_retries: int = MAX_RETRIES,
) -> CommitResult:
    """The 5-step commit: (1) optimistic version check, (2) winner -> canonical,
    (3) loser -> superseded, (4) subjects.canonical_belief_id + version++,
    (5) insert resolutions. Steps 1+4 are combined into one UPDATE. Retries on
    SQLSTATE 40001 (SerializationFailure) with exponential backoff; raises
    StaleResolutionError immediately (no retry) if the version check fails,
    since retrying the same decision against changed data would be wrong."""
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        conn = get_connection(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE subjects
                    SET version = version + 1, canonical_belief_id = %s, updated_at = now()
                    WHERE subject_key = %s AND version = %s
                    RETURNING version
                    """,
                    (winner_belief_id, subject_key, expected_version),
                )
                row = cur.fetchone()
                if row is None:
                    raise StaleResolutionError(subject_key, expected_version)
                new_version = row[0]

                cur.execute("UPDATE beliefs SET status = 'canonical' WHERE id = %s", (winner_belief_id,))
                cur.execute(
                    "UPDATE beliefs SET status = 'superseded', superseded_by = %s WHERE id = %s",
                    (winner_belief_id, loser_belief_id),
                )

                resolution_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO resolutions
                        (id, subject_key, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence, resolved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (resolution_id, subject_key, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence),
                )
            conn.commit()
            return CommitResult(resolution_id=resolution_id, new_version=new_version, retries=attempt - 1)
        except StaleResolutionError:
            raise
        except psycopg2.errors.SerializationFailure as exc:
            last_error = exc
            if attempt == max_retries:
                raise
            _backoff_sleep(attempt)
            continue
        finally:
            conn.close()

    raise last_error  # pragma: no cover - unreachable, loop always returns or raises


def commit_contested(
    database_url: str | None,
    belief_a_id: UUID,
    belief_b_id: UUID,
    subject_key: str | None = None,
    winner_belief_id: UUID | None = None,
    loser_belief_id: UUID | None = None,
    verdict: str | None = None,
    reasoning: str | None = None,
    method: str | None = None,
    confidence: float | None = None,
    max_retries: int = MAX_RETRIES,
) -> CommitResult:
    """For needs_human=True decisions: marks both conflicting beliefs
    'contested' without promoting either to canonical or touching
    subjects.canonical_belief_id/version - nothing is confirmed yet.

    Schema gap, flagged rather than worked around silently: resolutions.
    winner_belief_id and loser_belief_id are NOT NULL in the Block 1A schema.
    The arbiter can genuinely return winner="neither" (observed in real,
    non-mocked Bedrock calls during the Block 2A checkpoint - see
    docs/REVIEW_LOG.md), which has no valid (winner, loser) pair to store.
    Rather than fabricate a winner or silently redesign the schema, this
    function only inserts a resolutions row when winner_belief_id and
    loser_belief_id are both provided (i.e. the arbiter picked A or B, just
    not confidently enough to auto-commit); for a true "neither" outcome it
    marks both beliefs contested and returns without a resolutions row.
    """
    for attempt in range(1, max_retries + 1):
        conn = get_connection(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE beliefs SET status = 'contested' WHERE id IN (%s, %s)", (belief_a_id, belief_b_id))

                resolution_id = None
                if winner_belief_id is not None and loser_belief_id is not None:
                    resolution_id = uuid.uuid4()
                    cur.execute(
                        """
                        INSERT INTO resolutions
                            (id, subject_key, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence, resolved_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                        """,
                        (resolution_id, subject_key, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence),
                    )
            conn.commit()
            return CommitResult(resolution_id=resolution_id, new_version=None, retries=attempt - 1)
        except psycopg2.errors.SerializationFailure:
            if attempt == max_retries:
                raise
            _backoff_sleep(attempt)
            continue
        finally:
            conn.close()
