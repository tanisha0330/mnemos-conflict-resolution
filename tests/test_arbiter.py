from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.resolution.arbiter import (
    ArbiterClaim,
    ArbiterInput,
    arbitrate,
)

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def make_input(tier_a=3, tier_b=2) -> ArbiterInput:
    return ArbiterInput(
        subject_key="test:subject",
        claim_a=ArbiterClaim("A", "claim A text", "source-a", tier_a, 0.8, NOW),
        claim_b=ArbiterClaim("B", "claim B text", "source-b", tier_b, 0.7, NOW),
    )


def tool_response(input_dict: dict) -> dict:
    return {"output": {"message": {"content": [{"toolUse": {"name": "record_arbitration_decision", "input": input_dict}}]}}}


def malformed_response() -> dict:
    """No toolUse block at all - simulates the model refusing to call the tool."""
    return {"output": {"message": {"content": [{"text": "I'm not sure how to answer that."}]}}}


def valid_decision(**overrides) -> dict:
    base = {
        "winner": "A",
        "verdict": "contradiction",
        "reasoning": "Claim A is from a more reliable process.",
        "confidence": 0.9,
        "needs_human": False,
    }
    base.update(overrides)
    return base


# --- all 4 verdict types pass through correctly ------------------------------

@pytest.mark.parametrize("verdict", ["contradiction", "refinement", "temporal_shift", "both_valid"])
def test_verdict_types_pass_through(verdict):
    client = MagicMock()
    client.converse.return_value = tool_response(valid_decision(verdict=verdict))
    decision = arbitrate(make_input(tier_a=5, tier_b=2), client=client, primary_model_id="model-x")
    assert decision.verdict == verdict
    assert decision.winner == "A"
    assert decision.needs_human is False  # confidence high, tiers differ, model said False


# --- escalation rules enforced regardless of what the model says -------------

def test_escalates_on_low_confidence_even_if_model_says_no():
    client = MagicMock()
    client.converse.return_value = tool_response(valid_decision(confidence=0.4, needs_human=False))
    decision = arbitrate(make_input(tier_a=5, tier_b=2), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


def test_escalates_on_equal_authority_even_if_model_says_no():
    client = MagicMock()
    client.converse.return_value = tool_response(valid_decision(confidence=0.95, needs_human=False))
    decision = arbitrate(make_input(tier_a=3, tier_b=3), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


def test_respects_model_needs_human_true():
    client = MagicMock()
    client.converse.return_value = tool_response(valid_decision(confidence=0.95, needs_human=True))
    decision = arbitrate(make_input(tier_a=5, tier_b=2), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


def test_no_escalation_when_confident_and_unequal_authority():
    client = MagicMock()
    client.converse.return_value = tool_response(valid_decision(confidence=0.9, needs_human=False))
    decision = arbitrate(make_input(tier_a=5, tier_b=2), client=client, primary_model_id="model-x")
    assert decision.needs_human is False


# --- refinement exception to the equal-authority forced escalation ------------
# See docs/REVIEW_LOG.md Known Problem #2: refinement is structurally
# non-destructive (adds detail, doesn't assert the older claim was wrong), so
# a confident refinement verdict is allowed to autonomously commit even under
# equal authority. Every other verdict type keeps the forced escalation.

def test_confident_refinement_with_equal_authority_does_not_escalate():
    client = MagicMock()
    client.converse.return_value = tool_response(
        valid_decision(verdict="refinement", winner="B", confidence=0.9, needs_human=False)
    )
    decision = arbitrate(make_input(tier_a=3, tier_b=3), client=client, primary_model_id="model-x")
    assert decision.needs_human is False
    assert decision.verdict == "refinement"


def test_low_confidence_refinement_with_equal_authority_still_escalates():
    client = MagicMock()
    client.converse.return_value = tool_response(
        valid_decision(verdict="refinement", winner="B", confidence=0.5, needs_human=False)
    )
    decision = arbitrate(make_input(tier_a=3, tier_b=3), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


def test_refinement_with_equal_authority_still_escalates_if_model_flags_human():
    client = MagicMock()
    client.converse.return_value = tool_response(
        valid_decision(verdict="refinement", winner="B", confidence=0.9, needs_human=True)
    )
    decision = arbitrate(make_input(tier_a=3, tier_b=3), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


@pytest.mark.parametrize("verdict", ["contradiction", "temporal_shift", "both_valid"])
def test_non_refinement_verdicts_still_escalate_on_equal_authority_even_confident(verdict):
    client = MagicMock()
    client.converse.return_value = tool_response(
        valid_decision(verdict=verdict, winner="B", confidence=0.95, needs_human=False)
    )
    decision = arbitrate(make_input(tier_a=3, tier_b=3), client=client, primary_model_id="model-x")
    assert decision.needs_human is True


# --- retry on malformed output -------------------------------------------------

def test_retries_on_malformed_then_succeeds():
    client = MagicMock()
    client.converse.side_effect = [malformed_response(), tool_response(valid_decision())]
    decision = arbitrate(make_input(), client=client, primary_model_id="model-x")
    assert decision.winner == "A"
    assert decision.model_id == "model-x"
    assert decision.attempts == 2
    assert client.converse.call_count == 2


def test_retries_on_invalid_field_values():
    client = MagicMock()
    client.converse.side_effect = [
        tool_response({"winner": "C", "verdict": "contradiction", "reasoning": "x", "confidence": 0.9, "needs_human": False}),  # invalid winner
        tool_response(valid_decision()),
    ]
    decision = arbitrate(make_input(), client=client, primary_model_id="model-x")
    assert decision.winner == "A"
    assert client.converse.call_count == 2


# --- fallback to second model after exhausting retries ------------------------

def test_falls_back_to_second_model_after_max_retries():
    client = MagicMock()
    client.converse.side_effect = [
        malformed_response(), malformed_response(), malformed_response(),  # primary: 1 + 2 retries, all fail
        tool_response(valid_decision(winner="B")),  # fallback succeeds immediately
    ]
    decision = arbitrate(make_input(), client=client, primary_model_id="model-x", fallback_model_id="model-y")
    assert decision.model_id == "model-y"
    assert decision.winner == "B"
    assert client.converse.call_count == 4


def test_safe_fallback_when_both_models_exhaust_retries():
    client = MagicMock()
    client.converse.side_effect = [malformed_response()] * 6  # 3 attempts x 2 models, all fail
    decision = arbitrate(make_input(), client=client, primary_model_id="model-x", fallback_model_id="model-y")
    assert decision.needs_human is True
    assert decision.winner == "neither"
    assert client.converse.call_count == 6
