from unittest.mock import MagicMock

from src.ingestion.claim_extraction import extract_claim_text


def test_extract_claim_text_returns_model_text():
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": '"Order-12345 refund was processed."'}]}}
    }
    result = extract_claim_text("stripe webhook: refund.processed order_id=12345", client=client, model_id="model-x")
    assert result == "Order-12345 refund was processed."
    client.converse.assert_called_once()
