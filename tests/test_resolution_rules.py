from datetime import datetime, timedelta, timezone

import pytest

from src.resolution.rules import CONFIDENCE_FLOOR, RECENCY_MULTIPLE, RuleCandidate, RuleOutcome, apply_rules

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def candidate(authority_tier=3, confidence=0.8, observed_at=None) -> RuleCandidate:
    return RuleCandidate(authority_tier=authority_tier, confidence=confidence, observed_at=observed_at or NOW)


# --- Rule 1: authority tier ---------------------------------------------------

def test_higher_authority_tier_wins_existing():
    existing = candidate(authority_tier=5)
    new = candidate(authority_tier=2)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.EXISTING_WINS
    assert result.rule == "authority_tier"


def test_higher_authority_tier_wins_new():
    existing = candidate(authority_tier=2)
    new = candidate(authority_tier=5)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "authority_tier"


def test_authority_tier_beats_everything_else():
    # even if new has terrible confidence and is ancient, a strictly higher
    # tier wins outright before recency/confidence are ever considered.
    existing = candidate(authority_tier=1, confidence=0.99, observed_at=NOW)
    new = candidate(authority_tier=2, confidence=0.01, observed_at=NOW - timedelta(days=365))
    result = apply_rules(existing, new, "volatile", NOW)
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "authority_tier"


def test_real_seed_conflict_stripe_beats_zendesk():
    # mirrors the seeded order-12345 pair: stripe_api (tier 5) vs zendesk_tickets (tier 3)
    stripe = candidate(authority_tier=5, confidence=0.98, observed_at=NOW - timedelta(hours=1))
    zendesk = candidate(authority_tier=3, confidence=0.75, observed_at=NOW - timedelta(hours=3))

    assert apply_rules(existing=stripe, new=zendesk, volatility="volatile", now=NOW).outcome == RuleOutcome.EXISTING_WINS
    assert apply_rules(existing=zendesk, new=stripe, volatility="volatile", now=NOW).outcome == RuleOutcome.NEW_WINS


# --- Rule 2: recency (volatile subjects only) ---------------------------------

def test_recency_rule_only_applies_when_volatile():
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(days=100))
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(hours=1))
    result = apply_rules(existing, new, "stable", NOW)
    # tier equal, recency doesn't apply on a stable subject, confidence equal and
    # both above the floor -> nothing decides it
    assert result.outcome == RuleOutcome.NEEDS_LLM


def test_recency_new_much_fresher_wins_on_volatile_subject():
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(days=100))
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(hours=1))
    result = apply_rules(existing, new, "volatile", NOW)
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "recency"


def test_recency_existing_much_fresher_wins_on_volatile_subject():
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(hours=1))
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - timedelta(days=100))
    result = apply_rules(existing, new, "volatile", NOW)
    assert result.outcome == RuleOutcome.EXISTING_WINS
    assert result.rule == "recency"


def test_recency_ratio_exactly_at_boundary_does_not_fire():
    # exactly RECENCY_MULTIPLE x is not ">10x more recent" (strict), so this
    # should fall through to the confidence-floor rule, not decide on recency.
    existing_age = timedelta(seconds=1000)
    new_age = existing_age / RECENCY_MULTIPLE
    existing = candidate(authority_tier=3, confidence=0.1, observed_at=NOW - existing_age)
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - new_age)
    result = apply_rules(existing, new, "volatile", NOW)
    # recency didn't fire (exact boundary) -> falls to confidence floor, existing < 0.3 loses
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "confidence_floor"


def test_recency_ratio_just_over_boundary_fires():
    existing_age = timedelta(seconds=1000)
    new_age = existing_age / (RECENCY_MULTIPLE + 1)
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - existing_age)
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW - new_age)
    result = apply_rules(existing, new, "volatile", NOW)
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "recency"


# --- Rule 3: confidence floor ---------------------------------------------------

def test_confidence_below_floor_loses():
    existing = candidate(authority_tier=3, confidence=0.2, observed_at=NOW)
    new = candidate(authority_tier=3, confidence=0.8, observed_at=NOW)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.NEW_WINS
    assert result.rule == "confidence_floor"


def test_new_confidence_below_floor_loses():
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW)
    new = candidate(authority_tier=3, confidence=0.2, observed_at=NOW)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.EXISTING_WINS
    assert result.rule == "confidence_floor"


def test_confidence_exactly_at_floor_is_not_below():
    # CONFIDENCE_FLOOR itself is not "below" the floor -> rule 3 shouldn't fire
    existing = candidate(authority_tier=3, confidence=CONFIDENCE_FLOOR, observed_at=NOW)
    new = candidate(authority_tier=3, confidence=0.9, observed_at=NOW)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.NEEDS_LLM


def test_both_below_floor_does_not_decide():
    existing = candidate(authority_tier=3, confidence=0.1, observed_at=NOW)
    new = candidate(authority_tier=3, confidence=0.2, observed_at=NOW)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.NEEDS_LLM


# --- Fallthrough ------------------------------------------------------------

def test_no_rule_applies_needs_llm():
    existing = candidate(authority_tier=3, confidence=0.8, observed_at=NOW)
    new = candidate(authority_tier=3, confidence=0.7, observed_at=NOW)
    result = apply_rules(existing, new, "stable", NOW)
    assert result.outcome == RuleOutcome.NEEDS_LLM
    assert result.rule == "none"
