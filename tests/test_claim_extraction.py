from unittest.mock import MagicMock

from src.ingestion.claim_extraction import extract_claim_text


def test_extraction_preserves_carrier_and_tracking_detail_real_bedrock():
    # Regression test for a real bug: the model sometimes normalized "shipped
    # via FedEx, tracking number 884213" down to just "shipped", dropping the
    # carrier and tracking number entirely - which then sent two claims about
    # the same order to different subject_keys (shipping_carrier vs
    # order_status) and silently broke conflict detection between them. Real,
    # non-mocked calls (10x, since this is exactly the kind of variance that
    # slipped through before) against live Bedrock, same bar as the Known
    # Problem #1 threshold fix's re-measurement.
    raw_text = "Order-77821 has shipped via FedEx, tracking number 884213"
    for _ in range(10):
        claim = extract_claim_text(raw_text)
        claim_lower = claim.lower()
        assert "fedex" in claim_lower, f"dropped carrier: {claim!r}"
        assert "884213" in claim, f"dropped tracking number: {claim!r}"


def test_extract_claim_text_returns_model_text():
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": '"Order-12345 refund was processed."'}]}}
    }
    result = extract_claim_text("stripe webhook: refund.processed order_id=12345", client=client, model_id="model-x")
    assert result == "Order-12345 refund was processed."
    client.converse.assert_called_once()
