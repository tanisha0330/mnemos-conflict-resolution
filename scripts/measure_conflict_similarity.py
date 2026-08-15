"""Measures real cosine similarity between the flagship demo's refund-conflict
claim pair (order-12345: "refund pending" vs "refund processed"), run N times
through the real extract_claim_text -> generate_embedding pipeline to capture
LLM-paraphrasing variance run-to-run. Ad hoc diagnostic script for
docs/REVIEW_LOG.md Known Problem #1 -- not part of the test suite."""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from src.ingestion.claim_extraction import extract_claim_text
from src.ingestion.embeddings import generate_embedding

import uuid

N_RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def raw_pair():
    order_id = uuid.uuid4().hex[:8]  # matches src/demo/app.py's actual order_id generation
    return (
        f"Zendesk ticket: customer says refund for order-{order_id} is still pending",
        f"Stripe webhook: refund.processed for order-{order_id}",
    )


def cosine_similarity(a, b):
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    scores = []
    for i in range(1, N_RUNS + 1):
        raw_a, raw_b = raw_pair()
        claim_a = extract_claim_text(raw_a)
        claim_b = extract_claim_text(raw_b)
        emb_a = generate_embedding(claim_a)
        emb_b = generate_embedding(claim_b)
        sim = cosine_similarity(emb_a, emb_b)
        scores.append(sim)
        print(f"run {i:2d}: sim={sim:.4f}  claim_a={claim_a!r}  claim_b={claim_b!r}")

    arr = np.array(scores)
    print()
    print(f"n={len(arr)}  min={arr.min():.4f}  max={arr.max():.4f}  mean={arr.mean():.4f}  std={arr.std():.4f}")
    print(f"scores: {[round(s, 4) for s in scores]}")


if __name__ == "__main__":
    main()
