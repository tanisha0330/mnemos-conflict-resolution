"""Ground-truth verification: for a verifiable subject, check a real
system-of-record directly, instead of only weighing two claims against each
other via authority/recency/confidence heuristics (src.resolution.rules).
This runs *before* those heuristics in the pipeline - actual verified state
should outrank a proxy for trust, not just tiebreak it.

Generalized as a registry: each attribute (the part of a subject_key before
the ":", e.g. "refund_status") maps to a Verifier function supplied by
whichever domain needs it. src.verification.ledger's refund_status verifier
is registered below as the one built-in example; any other industry adds
its own via register_verifier() without touching this file's dispatch logic
or the pipeline call sites.

Deliberately narrow per-verifier contract: cheap and auditable (no LLM call
required), and required to return decided=False whenever it can't
confidently resolve the conflict, rather than guessing. That fallthrough is
what makes it safe to run unconditionally ahead of every other resolution
mechanism - an unregistered attribute, or a verifier that can't confirm
either side, never produces a wrong-but-confident answer, only "not
applicable here."
"""

import re
from dataclasses import dataclass
from typing import Callable

_SUBJECT_KEY_PATTERN = re.compile(r"^([a-z_]+):[a-z]+-(.+)$")


@dataclass(frozen=True)
class VerificationResult:
    decided: bool
    winner: str | None = None  # "existing" | "new"
    reason: str | None = None


# conn, entity_id, existing_claim_text, new_claim_text -> VerificationResult
Verifier = Callable[[object, str, str, str], VerificationResult]

_VERIFIERS: dict[str, Verifier] = {}


def register_verifier(attribute: str, verifier: Verifier) -> None:
    """Registers a ground-truth verifier for subject_keys of the form
    "{attribute}:{entity_type}-{entity_id}". Overwrites any existing
    registration for the same attribute (last registration wins)."""
    _VERIFIERS[attribute] = verifier


def verify_against_ledger(conn, subject_key: str, existing_claim_text: str, new_claim_text: str) -> VerificationResult:
    match = _SUBJECT_KEY_PATTERN.match(subject_key)
    if match is None:
        return VerificationResult(decided=False)

    attribute, entity_id = match.groups()
    verifier = _VERIFIERS.get(attribute)
    if verifier is None:
        return VerificationResult(decided=False)

    return verifier(conn, entity_id, existing_claim_text, new_claim_text)


def _register_builtin_verifiers() -> None:
    from src.verification.ledger import verify_refund_status
    from src.verification.shipment_ledger import verify_shipping_carrier

    register_verifier("refund_status", verify_refund_status)
    register_verifier("shipping_carrier", verify_shipping_carrier)


_register_builtin_verifiers()
