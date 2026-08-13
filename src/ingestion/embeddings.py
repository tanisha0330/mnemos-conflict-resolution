"""Generates embeddings via Amazon Titan Text Embeddings V2 on Bedrock.
Titan embedding models use the invoke_model API (not Converse) with a
model-specific JSON request/response body."""

import json
import os

import boto3

EMBEDDING_DIM = 1024  # must match beliefs.embedding VECTOR(1024) in the schema


def _get_client(region_name: str | None = None):
    return boto3.client("bedrock-runtime", region_name=region_name or os.environ.get("AWS_REGION"))


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
