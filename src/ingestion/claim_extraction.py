"""Extracts a clear, single-sentence factual claim from raw agent-observed
text (e.g. a mock Stripe/Zendesk payload) via Bedrock."""

import os

import boto3


def _get_client(region_name: str | None = None):
    return boto3.client("bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"))


def extract_claim_text(raw_text: str, client=None, model_id: str | None = None) -> str:
    client = client or _get_client()
    model_id = model_id or os.environ.get("BEDROCK_ARBITER_MODEL_ID")

    prompt = f"""Extract exactly one clear, concise factual claim from the following text, as a single plain sentence. State it directly (e.g. "Order-12345's refund was processed"), don't add hedging, don't add commentary, don't quote the source. If the text already is a clear claim, lightly clean it up rather than rewriting it unnecessarily.

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
