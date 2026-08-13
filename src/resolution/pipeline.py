"""Ties Stage 1 (detection) and Stage 2 (rules) into a single entry point.
Stops cleanly at PipelineOutcome.NEEDS_LLM without ever calling an LLM itself -
that's the Block 2A arbiter's job to consume.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from src.resolution.detection import DetectionOutcome, detect_conflict
from src.resolution.rules import RuleCandidate, RuleOutcome, apply_rules


class PipelineOutcome(str, Enum):
    NO_CANONICAL = "no_canonical"  # first belief for this subject
    DUPLICATE = "duplicate"        # near-identical to canonical; refresh observed_at, discard new
    NO_CONFLICT = "no_conflict"    # unrelated; insert as a new independent candidate
    RULE_DECIDED = "rule_decided"  # Stage 2 resolved it deterministically
    NEEDS_LLM = "needs_llm"        # Stage 2 couldn't decide; hand off to the Block 2A arbiter


@dataclass(frozen=True)
class PipelineResult:
    outcome: PipelineOutcome
    canonical_belief_id: UUID | None
    similarity: float | None
    rule_outcome: RuleOutcome | None = None
    rule_name: str | None = None
    reason: str | None = None


def evaluate_new_belief(
    conn,
    subject_key: str,
    new_embedding,
    new_candidate: RuleCandidate,
    volatility: str,
    now: datetime | None = None,
) -> PipelineResult:
    now = now or datetime.now(timezone.utc)

    detection = detect_conflict(conn, subject_key, new_embedding)

    if detection.outcome == DetectionOutcome.NO_CANONICAL:
        return PipelineResult(PipelineOutcome.NO_CANONICAL, None, None)
    if detection.outcome == DetectionOutcome.DUPLICATE:
        return PipelineResult(PipelineOutcome.DUPLICATE, detection.canonical_belief_id, detection.similarity)
    if detection.outcome == DetectionOutcome.NO_CONFLICT:
        return PipelineResult(PipelineOutcome.NO_CONFLICT, detection.canonical_belief_id, detection.similarity)

    existing = _load_rule_candidate(conn, detection.canonical_belief_id)
    rule_result = apply_rules(existing, new_candidate, volatility, now)

    outcome = PipelineOutcome.NEEDS_LLM if rule_result.outcome == RuleOutcome.NEEDS_LLM else PipelineOutcome.RULE_DECIDED
    return PipelineResult(
        outcome,
        detection.canonical_belief_id,
        detection.similarity,
        rule_result.outcome,
        rule_result.rule,
        rule_result.reason,
    )


def _load_rule_candidate(conn, belief_id: UUID) -> RuleCandidate:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.authority_tier, b.confidence, b.observed_at
            FROM beliefs b JOIN sources s ON s.id = b.source_id
            WHERE b.id = %s
            """,
            (belief_id,),
        )
        authority_tier, confidence, observed_at = cur.fetchone()
    return RuleCandidate(authority_tier=authority_tier, confidence=confidence, observed_at=observed_at)
