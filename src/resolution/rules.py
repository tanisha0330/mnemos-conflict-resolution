"""Stage 2 of conflict resolution: deterministic rules, tried in order before
any LLM call. Pure functions (no DB, no network) so every branch is directly
unit-testable and the LLM stage (Block 2A) has a single, explicit plug point:
RuleOutcome.NEEDS_LLM.

Rule order (first match wins):
  1. authority_tier: strictly higher tier wins outright.
  2. recency: only on volatile subjects, only when one claim is >10x more
     recent (by wall-clock age at evaluation time) than the other.
  3. confidence_floor: a claim below CONFIDENCE_FLOOR loses to any claim at
     or above it.
  Otherwise: NEEDS_LLM.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

CONFIDENCE_FLOOR = 0.3
RECENCY_MULTIPLE = 10
_EPSILON = 1e-9


class RuleOutcome(str, Enum):
    EXISTING_WINS = "existing_wins"
    NEW_WINS = "new_wins"
    NEEDS_LLM = "needs_llm"


@dataclass(frozen=True)
class RuleCandidate:
    """The subset of belief/source fields the deterministic rules need."""
    authority_tier: int
    confidence: float
    observed_at: datetime


@dataclass(frozen=True)
class RuleResult:
    outcome: RuleOutcome
    rule: str  # which rule fired: "authority_tier" | "recency" | "confidence_floor" | "none"
    reason: str  # human-readable explanation, suitable for resolutions.reasoning


def apply_rules(existing: RuleCandidate, new: RuleCandidate, volatility: str, now: datetime) -> RuleResult:
    # Rule 1: authority tier
    if existing.authority_tier != new.authority_tier:
        if existing.authority_tier > new.authority_tier:
            return RuleResult(
                RuleOutcome.EXISTING_WINS, "authority_tier",
                f"existing source authority_tier={existing.authority_tier} outranks "
                f"new source authority_tier={new.authority_tier}",
            )
        return RuleResult(
            RuleOutcome.NEW_WINS, "authority_tier",
            f"new source authority_tier={new.authority_tier} outranks "
            f"existing source authority_tier={existing.authority_tier}",
        )

    # Rule 2: recency, volatile subjects only. "More recent" is measured as wall-clock
    # age at evaluation time (now - observed_at); one side must be >10x fresher than
    # the other. This interpretation isn't spelled out in the build doc/CLAUDE.md beyond
    # ">10x more recent" - logged as a design decision in docs/REVIEW_LOG.md.
    if volatility == "volatile":
        age_existing = max((now - existing.observed_at).total_seconds(), 0.0)
        age_new = max((now - new.observed_at).total_seconds(), 0.0)

        if age_existing > age_new and age_existing > RECENCY_MULTIPLE * max(age_new, _EPSILON):
            ratio = age_existing / max(age_new, _EPSILON)
            return RuleResult(
                RuleOutcome.NEW_WINS, "recency",
                f"new claim is {ratio:.1f}x more recent than existing on a volatile subject "
                f"(existing age={age_existing:.0f}s, new age={age_new:.0f}s)",
            )
        if age_new > age_existing and age_new > RECENCY_MULTIPLE * max(age_existing, _EPSILON):
            ratio = age_new / max(age_existing, _EPSILON)
            return RuleResult(
                RuleOutcome.EXISTING_WINS, "recency",
                f"existing claim is {ratio:.1f}x more recent than new on a volatile subject "
                f"(new age={age_new:.0f}s, existing age={age_existing:.0f}s)",
            )

    # Rule 3: confidence floor
    existing_below = existing.confidence < CONFIDENCE_FLOOR
    new_below = new.confidence < CONFIDENCE_FLOOR
    if existing_below and not new_below:
        return RuleResult(
            RuleOutcome.NEW_WINS, "confidence_floor",
            f"existing confidence={existing.confidence} is below the floor ({CONFIDENCE_FLOOR}); "
            f"new confidence={new.confidence} is not",
        )
    if new_below and not existing_below:
        return RuleResult(
            RuleOutcome.EXISTING_WINS, "confidence_floor",
            f"new confidence={new.confidence} is below the floor ({CONFIDENCE_FLOOR}); "
            f"existing confidence={existing.confidence} is not",
        )

    return RuleResult(RuleOutcome.NEEDS_LLM, "none", "no deterministic rule applied; escalate to LLM arbiter")
