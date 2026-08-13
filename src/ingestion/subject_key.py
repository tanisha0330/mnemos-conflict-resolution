"""Assigns a subject_key to a claim: a simple entity+attribute heuristic
first, falling back to an LLM call only when the heuristic can't confidently
identify both an attribute category and an entity id. Getting this wrong
silently breaks conflict detection downstream (two claims about the same
real thing land under different subject_keys and never get compared) - see
docs/REVIEW_LOG.md for the Block 3A checkpoint spot-check.
"""

import os
import re

import boto3

# Checked in order - more specific phrases first, so e.g. "shipped via FedEx"
# doesn't fall into a generic order-status bucket.
ATTRIBUTE_KEYWORDS: dict[str, list[str]] = {
    "shipping_carrier": ["shipped via", "carrier is", "fedex", "ups ", "usps", "dhl"],
    "shipping_address": ["shipping address", "ship to", "delivery address"],
    "refund_status": ["refund"],
    "subscription_status": ["subscription"],
    "user_email": ["email"],
    "stock_status": ["in stock", "out of stock", "inventory level"],
    "tracking_number": ["tracking number", "tracking id"],
    "payment_status": ["payment was", "charge was", "charged $", "payment status"],
    "delivery_date": ["delivery date", "arrive by", "estimated arrival", "eta"],
    "order_status": ["order status", "order was cancelled", "order was canceled", "order is cancelled"],
}

ENTITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("order", re.compile(r"\border[-_ #]?(\w[\w-]*)", re.IGNORECASE)),
    ("user", re.compile(r"\buser[-_ #]?(\w[\w-]*)", re.IGNORECASE)),
    ("sku", re.compile(r"\bsku[-_ #]?(\w[\w-]*)", re.IGNORECASE)),
    ("ticket", re.compile(r"\bticket[-_ #]?(\w[\w-]*)", re.IGNORECASE)),
]

SUBJECT_KEY_TOOL = {
    "toolSpec": {
        "name": "record_subject_key",
        "description": "Records the entity+attribute this claim is about.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "attribute": {"type": "string", "description": "snake_case attribute category, e.g. refund_status, shipping_carrier, user_email"},
                    "entity_type": {"type": "string", "description": "lowercase entity type, e.g. order, user, sku, ticket"},
                    "entity_id": {"type": "string", "description": "the specific entity's id/number as it appears in the text"},
                },
                "required": ["attribute", "entity_type", "entity_id"],
            }
        },
    }
}


def assign_subject_key_heuristic(claim_text: str) -> str | None:
    text_lower = claim_text.lower()

    attribute = None
    for attr, keywords in ATTRIBUTE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            attribute = attr
            break
    if attribute is None:
        return None

    for entity_type, pattern in ENTITY_PATTERNS:
        match = pattern.search(claim_text)
        if match:
            entity_id = match.group(1).strip("-_").lower()
            # require at least one digit: every real id in this domain has one
            # (order-12345, user-789, sku-9012, XJ-4471) - without this check,
            # phrasing like "User with ID 902..." matches "with" as the id
            # (found during the Block 3A checkpoint spot-check, see
            # docs/REVIEW_LOG.md) and silently sends claims about the same
            # real entity to different subject_keys, never comparing them.
            if entity_id and any(ch.isdigit() for ch in entity_id):
                return f"{attribute}:{entity_type}-{entity_id}"
    return None


def _get_client(region_name: str | None = None):
    return boto3.client("bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"))


def assign_subject_key_llm(claim_text: str, client=None, model_id: str | None = None) -> str:
    client = client or _get_client()
    model_id = model_id or os.environ.get("BEDROCK_ARBITER_MODEL_ID")

    prompt = f"""What entity and attribute is this claim about? Pick a concise snake_case attribute name (e.g. refund_status, shipping_carrier, user_email, order_status) and identify the specific entity type and id mentioned.

Claim: "{claim_text}"

Call record_subject_key with your answer."""

    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        toolConfig={"tools": [SUBJECT_KEY_TOOL], "toolChoice": {"tool": {"name": "record_subject_key"}}},
    )
    for block in response["output"]["message"]["content"]:
        if "toolUse" in block:
            data = block["toolUse"]["input"]
            attribute = str(data["attribute"]).strip().lower().replace(" ", "_")
            entity_type = str(data["entity_type"]).strip().lower().replace(" ", "_")
            entity_id = str(data["entity_id"]).strip().lower()
            return f"{attribute}:{entity_type}-{entity_id}"
    raise ValueError(f"model {model_id} did not call record_subject_key")


def assign_subject_key(claim_text: str, client=None, model_id: str | None = None) -> str:
    heuristic_result = assign_subject_key_heuristic(claim_text)
    if heuristic_result is not None:
        return heuristic_result
    return assign_subject_key_llm(claim_text, client=client, model_id=model_id)
