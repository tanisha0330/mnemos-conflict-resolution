"""Ground-truth verification: for a verifiable-transaction subject, check a
real system-of-record (src.verification.ledger) directly, instead of only
weighing two claims against each other via authority/recency/confidence
heuristics (src.resolution.rules). This runs *before* those heuristics in
the pipeline - actual verified state should outrank a proxy for trust, not
just tiebreak it.

Deliberately narrow: a keyword match against a real ledger value, not an
LLM call, so it stays exactly as cheap and auditable as the deterministic
rules it runs ahead of. Falls through (decided=False) whenever it can't
confidently resolve the conflict - a subject the ledger doesn't cover, or a
ledger value that doesn't clearly match either claim - rather than guessing.
That fallthrough is not a failure case: it's what makes this safe to run
unconditionally ahead of every other resolution mechanism, since it can
never produce a wrong-but-confident answer, only "not applicable here."
"""

import re
from dataclasses import dataclass

from src.verification.ledger import get_refund_status

_ORDER_ID_PATTERN = re.compile(r"^refund_status:order-(.+)$")

_STATUS_KEYWORDS = {
    "processed": ["processed", "completed", "was refunded", "refund issued", "refunded successfully"],
    "pending": ["pending", "still pending", "not yet processed", "awaiting", "in progress",
                "hasn't been processed", "has not been processed"],
}


@dataclass(frozen=True)
class VerificationResult:
    decided: bool
    winner: str | None = None  # "existing" | "new"
    reason: str | None = None


def _matches_status(claim_text: str, status: str) -> bool:
    text_lower = claim_text.lower()
    return any(kw in text_lower for kw in _STATUS_KEYWORDS.get(status, []))


def verify_against_ledger(conn, subject_key: str, existing_claim_text: str, new_claim_text: str) -> VerificationResult:
    match = _ORDER_ID_PATTERN.match(subject_key)
    if match is None:
        return VerificationResult(decided=False)

    order_id = match.group(1)
    ledger_status = get_refund_status(conn, order_id)
    if ledger_status is None:
        return VerificationResult(decided=False)

    existing_matches = _matches_status(existing_claim_text, ledger_status)
    new_matches = _matches_status(new_claim_text, ledger_status)

    if existing_matches and not new_matches:
        return VerificationResult(
            decided=True, winner="existing",
            reason=(
                f"payment ledger (real system-of-record, independent of either claim) shows "
                f"refund_status={ledger_status!r} for order-{order_id}. This confirms the existing "
                f"claim ({existing_claim_text!r}) and contradicts the new claim ({new_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    if new_matches and not existing_matches:
        return VerificationResult(
            decided=True, winner="new",
            reason=(
                f"payment ledger (real system-of-record, independent of either claim) shows "
                f"refund_status={ledger_status!r} for order-{order_id}. This confirms the new "
                f"claim ({new_claim_text!r}) and contradicts the existing claim ({existing_claim_text!r}). "
                f"Decided by direct ground-truth verification, not the authority_tier/recency/"
                f"confidence heuristics - verified state outranks a proxy for trust."
            ),
        )
    return VerificationResult(decided=False)
