"""Generates embeddings via Amazon Titan Text Embeddings V2 on Bedrock.
Titan embedding models use the invoke_model API (not Converse) with a
model-specific JSON request/response body."""

import json
import os

import boto3
from botocore.config import Config

EMBEDDING_DIM = 1024  # must match beliefs.embedding VECTOR(1024) in the schema

# Adaptive mode: botocore's client-side rate limiting + retry-with-backoff on
# throttling and other transient errors (ThrottlingException, 5xx, timeouts).
# Without this, any Bedrock hiccup under real load crashes ingest() outright -
# unlike the DB writes, which already retry on SQLSTATE 40001.
_BEDROCK_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def _get_client(region_name: str | None = None):
    return boto3.client(
        "bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"), config=_BEDROCK_RETRY_CONFIG
    )


def generate_embedding(text: str, client=None, model_id: str | None = None) -> list[float]:
    client = client or _get_client()
    model_id = model_id or os.environ.get("BEDROCK_EMBEDDING_MODEL_ID")

    body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIM, "normalize": True})
    response = client.invoke_model(modelId=model_id, body=body, contentType="application/json", accept="application/json")
    payload = json.loads(response["body"].read())
    embedding = payload["embedding"]
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"expected {EMBEDDING_DIM}-dim embedding from {model_id}, got {len(embedding)}")
    return embedding
