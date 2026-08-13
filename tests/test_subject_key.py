import pytest

from src.ingestion.subject_key import assign_subject_key_heuristic

# Realistic examples across the order/refund/shipping demo domain - this is
# what the Block 3A checkpoint spot-checks by hand in docs/REVIEW_LOG.md.
HEURISTIC_CASES = [
    ("Refund for order-12345 has been processed and completed", "refund_status:order-12345"),
    ("Order-12345's refund is still pending per support ticket #4521", "refund_status:order-12345"),
    ("order-98765 is shipped via FedEx, tracking 794658312", "shipping_carrier:order-98765"),
    ("Customer says order-98765 arrived via FedEx", "shipping_carrier:order-98765"),
    ("user-789's email is jane.doe@example.com", "user_email:user-789"),
    ("user-789 confirmed their email as jane.doe@example.com in ticket #3390", "user_email:user-789"),
    ("user-42's shipping address is 123 Main St, Apt 4B", "shipping_address:user-42"),
    ("product SKU-9012 is in stock at Warehouse-East", "stock_status:sku-9012"),
    ("user-77's subscription status is active", "subscription_status:user-77"),
]


@pytest.mark.parametrize("claim_text,expected", HEURISTIC_CASES)
def test_heuristic_realistic_examples(claim_text, expected):
    assert assign_subject_key_heuristic(claim_text) == expected


def test_heuristic_returns_none_when_no_attribute_matches():
    assert assign_subject_key_heuristic("The weather today is sunny and 72 degrees") is None


def test_heuristic_returns_none_when_attribute_found_but_no_entity():
    # "refund" keyword matches, but there's no order/user/sku/ticket id to anchor it to
    assert assign_subject_key_heuristic("A refund was issued") is None


def test_heuristic_prefers_more_specific_shipping_carrier_over_generic_order_status():
    result = assign_subject_key_heuristic("order-555 was shipped via UPS yesterday")
    assert result == "shipping_carrier:order-555"


def test_heuristic_is_case_insensitive_on_keywords():
    assert assign_subject_key_heuristic("ORDER-12345 REFUND has been processed") == "refund_status:order-12345"


def test_heuristic_rejects_english_word_as_entity_id():
    # regression test: "User with ID 902..." previously matched "with" as the
    # entity id via the user-id regex, since it's the word right after "user".
    # Found during the Block 3A checkpoint spot-check (docs/REVIEW_LOG.md).
    assert assign_subject_key_heuristic("User with ID 902 and email address alex.chen92@gmail.com is verified") is None
