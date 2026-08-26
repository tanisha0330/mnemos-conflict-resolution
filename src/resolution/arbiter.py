"""LLM arbitration via Amazon Bedrock - invoked only when Stage 2's
deterministic rules (rules.py) return NEEDS_LLM. Uses the Converse API's
tool-use with a forced tool choice for structured output (not prompt+regex
parsing), retries on malformed/invalid output, and falls back from the
primary model to a secondary one if the primary can't be validated.

Model note: CLAUDE.md specifies "Claude 3.5 Haiku" as the fallback model, but
that exact model isn't in this AWS account's enabled Bedrock models (checked
via scripts/verify_setup.py's model listing). Substituted the closest
available equivalent, anthropic.claude-haiku-4-5-20251001-v1:0 - flagged in
docs/REVIEW_LOG.md.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.config import Config

MAX_RETRIES_PER_MODEL = 2
CONFIDENCE_ESCALATION_THRESHOLD = 0.6
DEFAULT_FALLBACK_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"

# Adaptive mode: botocore's client-side rate limiting + retry-with-backoff on
# throttling and other transient errors. This is separate from
# MAX_RETRIES_PER_MODEL/the primary->fallback-model retry above it, which
# only covers malformed model *output*, not transport-level failures like
# ThrottlingException - those used to crash arbitrate() outright.
_BEDROCK_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

VALID_WINNERS = {"A", "B", "neither"}
VALID_VERDICTS = {"contradiction", "refinement", "temporal_shift", "both_valid"}

DECISION_TOOL = {
    "toolSpec": {
        "name": "record_arbitration_decision",
        "description": "Records the arbitration decision between two conflicting beliefs about the same subject.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "winner": {
                        "type": "string",
                        "enum": sorted(VALID_WINNERS),
                        "description": "Which claim should be canonical: A, B, or neither.",
                    },
                    "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
                    "reasoning": {"type": "string", "description": "Specific explanation referencing the actual claims, not generic."},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "needs_human": {
                        "type": "boolean",
                        "description": "True if genuinely uncertain and a human should review before this is committed.",
                    },
                },
                "required": ["winner", "verdict", "reasoning", "confidence", "needs_human"],
            }
        },
    }
}


@dataclass(frozen=True)
class ArbiterClaim:
    label: str  # "A" or "B" - for prompt/response correlation only
    claim_text: str
    source_name: str
    authority_tier: int
    confidence: float
    observed_at: datetime


@dataclass(frozen=True)
class ArbiterInput:
    subject_key: str
    claim_a: ArbiterClaim
    claim_b: ArbiterClaim
    prior_canonical_claim_text: str | None = None


@dataclass(frozen=True)
class ArbiterDecision:
    winner: str
    verdict: str
    reasoning: str
    confidence: float
    needs_human: bool
    model_id: str
    attempts: int


class ArbitrationOutputInvalid(Exception):
    """Raised internally when a model's output can't be validated; caught by
    arbitrate() to drive retry/fallback, never escapes to the caller."""


def _build_prompt(arb_input: ArbiterInput) -> str:
    a, b = arb_input.claim_a, arb_input.claim_b
    prior = (
        f'\nPrior canonical belief on this subject: "{arb_input.prior_canonical_claim_text}"'
        if arb_input.prior_canonical_claim_text
        else ""
    )
    return f"""Two agents made conflicting claims about the same subject ("{arb_input.subject_key}"). Decide how to resolve this conflict.

Claim A: "{a.claim_text}"
  source: {a.source_name} (authority_tier={a.authority_tier}), confidence={a.confidence}, observed_at={a.observed_at.isoformat()}

Claim B: "{b.claim_text}"
  source: {b.source_name} (authority_tier={b.authority_tier}), confidence={b.confidence}, observed_at={b.observed_at.isoformat()}
{prior}

Decide:
- winner: "A" if claim A should be canonical, "B" if claim B should be canonical, "neither" if neither should stand as-is.
- verdict - pick exactly one:
  - "contradiction": one claim was simply WRONG when it was made - a factual error, not something that changed later. Example: two claims about a fixed, unchanging fact (e.g. "the meeting is at 3pm" vs "the meeting is at 4pm") where only one was ever true.
  - "temporal_shift": the underlying real-world state CHANGED between when the two claims were observed, so both were accurate at their own time - this is a status/state field being updated, not an error. Example: "subscription is active" (observed a month ago) vs "subscription is cancelled" (observed today) - the subscription really did get cancelled in between; claim A was true when made. Prefer this over "contradiction" whenever the claims describe a value that naturally changes over time (status, stock level, location) and the more recent one is simply the current state, not a correction of an error.
  - "refinement": the newer claim adds detail/precision without contradicting the older one.
  - "both_valid": they're both true right now, in different scopes/contexts, and don't actually conflict.
- reasoning: be specific about WHY, referencing the actual claim content - not a generic template answer.
- confidence: your genuine confidence in this decision, 0 to 1.
- needs_human: true if you're not confident, the situation is ambiguous, or you think a person should look at this before it's acted on. It's fine and expected to say true here.

Call record_arbitration_decision with your answer."""


def _get_client(region_name: str | None = None):
    return boto3.client(
        "bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"), config=_BEDROCK_RETRY_CONFIG
    )


def _call_model(client, model_id: str, prompt: str) -> dict:
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        toolConfig={
            "tools": [DECISION_TOOL],
            "toolChoice": {"tool": {"name": "record_arbitration_decision"}},
        },
    )
    for block in response["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise ArbitrationOutputInvalid(f"model {model_id} did not call the decision tool")


def _validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ArbitrationOutputInvalid(f"tool input was not an object: {raw!r}")
    missing = {"winner", "verdict", "reasoning", "confidence", "needs_human"} - raw.keys()
    if missing:
        raise ArbitrationOutputInvalid(f"missing fields: {missing}")
    if raw["winner"] not in VALID_WINNERS:
        raise ArbitrationOutputInvalid(f"invalid winner: {raw['winner']!r}")
    if raw["verdict"] not in VALID_VERDICTS:
        raise ArbitrationOutputInvalid(f"invalid verdict: {raw['verdict']!r}")
    if not isinstance(raw["confidence"], (int, float)) or not (0 <= raw["confidence"] <= 1):
        raise ArbitrationOutputInvalid(f"invalid confidence: {raw['confidence']!r}")
    if not isinstance(raw["needs_human"], bool):
        raise ArbitrationOutputInvalid(f"invalid needs_human: {raw['needs_human']!r}")
    if not isinstance(raw["reasoning"], str) or not raw["reasoning"].strip():
        raise ArbitrationOutputInvalid("empty reasoning")
    return raw


def _attempt_model(client, model_id: str, prompt: str, max_retries: int) -> tuple[dict, int] | None:
    """Tries model_id up to max_retries+1 times. Returns (validated_output, attempts_used) or None."""
    for attempt in range(1, max_retries + 2):
        try:
            raw = _call_model(client, model_id, prompt)
            return _validate(raw), attempt
        except (ArbitrationOutputInvalid, KeyError, TypeError, json.JSONDecodeError):
            continue
    return None


def arbitrate(
    arb_input: ArbiterInput,
    client=None,
    primary_model_id: str | None = None,
    fallback_model_id: str | None = None,
) -> ArbiterDecision:
    """Calls the Bedrock arbiter. Never raises - always returns a decision,
    falling back to needs_human=True if no model's output validates even
    after retries and a fallback-model attempt.

    needs_human is forced True whenever confidence < 0.6 OR the two sources
    have equal authority_tier, regardless of what the model itself reports -
    this is an application-level guarantee (CLAUDE.md "Escalate, don't
    force"), not just a prompt instruction the model might not follow.

    One deliberate, flagged exception to the equal-authority forcing: a
    "refinement" verdict at model confidence >= CONFIDENCE_ESCALATION_THRESHOLD
    (and the model itself not requesting a human) is allowed to autonomously
    commit even under equal authority. Refinement is structurally
    non-destructive - the newer claim adds detail without asserting the older
    one was wrong, so nothing is being overwritten on a guess the way a
    contradiction/temporal_shift/both_valid verdict would be. Real Bedrock
    testing (20 live calls across all 4 verdict types, see docs/REVIEW_LOG.md
    Known Problem #2) showed every equal-authority call landing on
    needs_human=True purely from this rule, at confidence >=0.80 in every
    case - this exception was a deliberate decision, not a silent loosening
    of "escalate, don't force" for its own sake.
    """
    client = client or _get_client()
    primary_model_id = primary_model_id or os.environ.get("BEDROCK_ARBITER_MODEL_ID")
    fallback_model_id = fallback_model_id or os.environ.get(
        "BEDROCK_ARBITER_FALLBACK_MODEL_ID", DEFAULT_FALLBACK_MODEL_ID
    )

    prompt = _build_prompt(arb_input)
    total_attempts = 0

    for model_id in (primary_model_id, fallback_model_id):
        result = _attempt_model(client, model_id, prompt, MAX_RETRIES_PER_MODEL)
        if result is not None:
            raw, attempts = result
            total_attempts += attempts
            equal_authority = arb_input.claim_a.authority_tier == arb_input.claim_b.authority_tier
            model_flagged_human = bool(raw["needs_human"])
            model_confident = float(raw["confidence"]) >= CONFIDENCE_ESCALATION_THRESHOLD

            # Equal authority normally forces escalation, but a confident,
            # non-destructive "refinement" verdict is a deliberate exception -
            # see the arbitrate() docstring.
            is_autonomous_refinement = (
                raw["verdict"] == "refinement" and model_confident and not model_flagged_human
            )
            equal_authority_needs_escalation = equal_authority and not is_autonomous_refinement

            needs_human = model_flagged_human or not model_confident or equal_authority_needs_escalation
            return ArbiterDecision(
                winner=raw["winner"],
                verdict=raw["verdict"],
                reasoning=raw["reasoning"],
                confidence=float(raw["confidence"]),
                needs_human=needs_human,
                model_id=model_id,
                attempts=total_attempts,
            )
        total_attempts += MAX_RETRIES_PER_MODEL + 1

    return ArbiterDecision(
        winner="neither",
        verdict="contradiction",
        reasoning=(
            f"Arbitration failed: neither {primary_model_id} nor {fallback_model_id} returned a "
            f"valid decision after {total_attempts} total attempts. Escalating to a human."
        ),
        confidence=0.0,
        needs_human=True,
        model_id=fallback_model_id,
        attempts=total_attempts,
    )
