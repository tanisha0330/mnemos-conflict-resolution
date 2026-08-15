"""Diagnostic for docs/REVIEW_LOG.md Known Problem #2: the arbiter only ever
sees equal-authority conflicts (structurally guaranteed by rules.py - see
apply_rules(), which resolves any authority-tier mismatch before NEEDS_LLM
is reachable), so CLAUDE.md's "equal authority -> needs_human=true" rule
means every real arbiter call ends up escalated, regardless of the model's
own confidence or verdict.

This script calls the real arbiter (live Bedrock, not mocked) on four
hand-built equal-authority scenarios, one per verdict type, run several
times each to see whether refinement/temporal_shift/both_valid verdicts
come back with genuinely high model confidence -- i.e. whether the forced
escalation is discarding a signal the model was actually confident about,
or whether the model is itself uncertain on these cases regardless of the
equal-authority rule. Ad hoc diagnostic, not part of the test suite.
"""

import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

from src.resolution.arbiter import ArbiterClaim, ArbiterInput, arbitrate

NOW = datetime.now(timezone.utc)
EQUAL_TIER = 3
RUNS_PER_SCENARIO = 5

SCENARIOS = {
    "contradiction": ArbiterInput(
        subject_key="warehouse_manager:site-9",
        claim_a=ArbiterClaim("A", "The warehouse manager at site-9 is Alice Chen.", "hr_system_a", EQUAL_TIER, 0.9, NOW - timedelta(days=1)),
        claim_b=ArbiterClaim("B", "The warehouse manager at site-9 is Bob Nguyen.", "hr_system_b", EQUAL_TIER, 0.9, NOW - timedelta(hours=2)),
    ),
    "refinement": ArbiterInput(
        subject_key="shipping_address:cust-4471",
        claim_a=ArbiterClaim("A", "Customer 4471's shipping address is 123 Main St, Springfield.", "crm_a", EQUAL_TIER, 0.9, NOW - timedelta(days=3)),
        claim_b=ArbiterClaim("B", "Customer 4471's shipping address is 123 Main St, Apt 4B, Springfield.", "crm_b", EQUAL_TIER, 0.9, NOW - timedelta(hours=1)),
    ),
    "temporal_shift": ArbiterInput(
        subject_key="subscription_tier:cust-882",
        claim_a=ArbiterClaim("A", "Customer 882's subscription tier is Basic.", "billing_a", EQUAL_TIER, 0.9, NOW - timedelta(days=30)),
        claim_b=ArbiterClaim("B", "Customer 882's subscription tier is Premium.", "billing_b", EQUAL_TIER, 0.9, NOW - timedelta(minutes=10)),
    ),
    "both_valid": ArbiterInput(
        subject_key="stock_status:sku-2201",
        claim_a=ArbiterClaim("A", "SKU-2201 is in stock at the East warehouse.", "inventory_a", EQUAL_TIER, 0.9, NOW - timedelta(hours=5)),
        claim_b=ArbiterClaim("B", "SKU-2201 is out of stock at the West warehouse.", "inventory_b", EQUAL_TIER, 0.9, NOW - timedelta(hours=5)),
    ),
}


def main():
    print(f"Equal authority_tier={EQUAL_TIER} for both claims in every scenario (matches the real, structurally-guaranteed case).\n")
    for expected_verdict, arb_input in SCENARIOS.items():
        print(f"=== scenario: {expected_verdict} ===")
        for i in range(1, RUNS_PER_SCENARIO + 1):
            decision = arbitrate(arb_input)
            match = "OK " if decision.verdict == expected_verdict else "DIFF"
            print(
                f"  run {i}: verdict={decision.verdict:<14} [{match}] "
                f"confidence={decision.confidence:.2f} needs_human={decision.needs_human} "
                f"winner={decision.winner} model={decision.model_id}"
            )
        print()


if __name__ == "__main__":
    main()
