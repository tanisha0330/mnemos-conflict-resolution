"""Extracts a clear, single-sentence factual claim from raw agent-observed
text (e.g. a mock Stripe/Zendesk payload) via Bedrock."""

import os

import boto3
from botocore.config import Config

# Adaptive mode: botocore's client-side rate limiting + retry-with-backoff on
# throttling and other transient errors. Without this, any Bedrock hiccup
# under real load crashes ingest() outright - unlike the DB writes, which
# already retry on SQLSTATE 40001.
_BEDROCK_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def _get_client(region_name: str | None = None):
    return boto3.client(
        "bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"), config=_BEDROCK_RETRY_CONFIG
    )


def extract_claim_text(raw_text: str, client=None, model_id: str | None = None) -> str:
    client = client or _get_client()
    model_id = model_id or os.environ.get("BEDROCK_ARBITER_MODEL_ID")

    prompt = f"""Extract exactly one clear, concise factual claim from the following text, as a single plain sentence, in this exact form: "<Subject> <verb phrase stating the fact>" (e.g. "Order-12345's refund was processed", "Customer 4471's shipping address is 123 Main St"). Always rewrite into this normalized form - never pass through source jargon, field names, or event names verbatim (e.g. a raw "refund.processed" webhook event must become "was processed", not be kept as "refund.processed"; a "Zendesk ticket" or "Stripe webhook" mention describes the source, not the claim, so drop it from the sentence itself). Don't add hedging, don't add commentary, don't quote the source.

Text: "{raw_text}"

Respond with only the claim sentence, nothing else."""

    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.0},
    )
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            return block["text"].strip().strip('"')
    raise ValueError(f"model {model_id} returned no text content for claim extraction")
