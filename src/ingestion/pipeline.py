"""End-to-end ingestion: raw text + metadata -> extracted claim -> embedding ->
subject_key -> candidate belief -> Stage 1/2 conflict pipeline (Block 1B) ->
arbiter if needed (Block 2A) -> transactional commit (Block 2B).

This goes further than "insert as candidate belief triggering the Stage 1
conflict pipeline" (the literal Block 3A scope) by also wiring up the arbiter
and commit steps built in Block 2A/2B - without that, nothing in the system
would ever actually apply a resolution end-to-end, which the demo (Block 4B)
needs. Noted as a deliberate scope expansion, not scope creep for its own
sake: see docs/REVIEW_LOG.md.

Confidence note: extraction doesn't have a natural confidence score (it's
Bedrock's best reading of the raw text), so newly-ingested claims get a fixed
default confidence (0.85) unless the caller overrides it. This is a
reasonable placeholder, not a modeled value - flagged since Stage 2's
confidence-floor rule depends on it.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.ingestion.claim_extraction import extract_claim_text
from src.ingestion.embeddings import generate_embedding
from src.ingestion.subject_key import assign_subject_key
from src.resolution.arbiter import ArbiterClaim, ArbiterInput, arbitrate
from src.resolution.commit import commit_contested, commit_resolution
from src.resolution.pipeline import PipelineOutcome, evaluate_new_belief
from src.resolution.rules import RuleCandidate, RuleOutcome
from src.schema.db import get_connection

DEFAULT_EXTRACTED_CONFIDENCE = 0.85


@dataclass(frozen=True)
class IngestResult:
    belief_id: uuid.UUID | None
    subject_key: str
    claim_text: str
    outcome: str  # "canonical" | "duplicate" | "no_conflict" | "resolved_rule" | "resolved_llm" | "contested"
    detail: str | None = None


def _insert_belief(cur, belief_id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status):
    cur.execute(
        """
        INSERT INTO beliefs
            (id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (belief_id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, status),
    )


def _load_belief_for_arbiter(conn, belief_id) -> ArbiterClaim:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.claim_text, s.name, s.authority_tier, b.confidence, b.observed_at
            FROM beliefs b JOIN sources s ON s.id = b.source_id
            WHERE b.id = %s
            """,
            (belief_id,),
        )
        claim_text, source_name, authority_tier, confidence, observed_at = cur.fetchone()
    return ArbiterClaim("A", claim_text, source_name, authority_tier, float(confidence), observed_at)


def ingest(
    raw_text: str,
    agent_id: str,
    source_id: uuid.UUID,
    database_url: str | None = None,
    volatility_for_new_subjects: str = "stable",
    confidence: float = DEFAULT_EXTRACTED_CONFIDENCE,
) -> IngestResult:
    claim_text = extract_claim_text(raw_text)
    embedding = generate_embedding(claim_text)
    subject_key = assign_subject_key(claim_text)
    observed_at = datetime.now(timezone.utc)

    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, %s, now()) ON CONFLICT (subject_key) DO NOTHING",
                (subject_key, volatility_for_new_subjects),
            )
            cur.execute("SELECT version, volatility FROM subjects WHERE subject_key = %s", (subject_key,))
            expected_version, volatility = cur.fetchone()

            cur.execute("SELECT authority_tier FROM sources WHERE id = %s", (source_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"unknown source_id {source_id}")
            authority_tier = row[0]
        conn.commit()

        new_candidate = RuleCandidate(authority_tier=authority_tier, confidence=confidence, observed_at=observed_at)
        detection = evaluate_new_belief(conn, subject_key, embedding, new_candidate, volatility)

        if detection.outcome == PipelineOutcome.NO_CANONICAL:
            belief_id = uuid.uuid4()
            with conn.cursor() as cur:
                _insert_belief(cur, belief_id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, "canonical")
                cur.execute(
                    "UPDATE subjects SET canonical_belief_id = %s, version = version + 1, updated_at = now() WHERE subject_key = %s",
                    (belief_id, subject_key),
                )
            conn.commit()
            return IngestResult(belief_id, subject_key, claim_text, "canonical")

        if detection.outcome == PipelineOutcome.DUPLICATE:
            with conn.cursor() as cur:
                cur.execute("UPDATE beliefs SET observed_at = now() WHERE id = %s", (detection.canonical_belief_id,))
            conn.commit()
            return IngestResult(None, subject_key, claim_text, "duplicate", f"merged into existing belief {detection.canonical_belief_id}")

        if detection.outcome == PipelineOutcome.NO_CONFLICT:
            belief_id = uuid.uuid4()
            with conn.cursor() as cur:
                _insert_belief(cur, belief_id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, "candidate")
            conn.commit()
            return IngestResult(belief_id, subject_key, claim_text, "no_conflict")

        # CONFLICT: insert as candidate first, then resolve via rules or the arbiter
        belief_id = uuid.uuid4()
        with conn.cursor() as cur:
            _insert_belief(cur, belief_id, subject_key, claim_text, embedding, agent_id, source_id, confidence, observed_at, "candidate")
        conn.commit()

        existing_belief_id = detection.canonical_belief_id
        existing_claim = _load_belief_for_arbiter(conn, existing_belief_id)
    finally:
        conn.close()

    if detection.outcome == PipelineOutcome.RULE_DECIDED:
        if detection.rule_outcome == RuleOutcome.NEW_WINS:
            winner, loser = belief_id, existing_belief_id
        else:
            winner, loser = existing_belief_id, belief_id
        commit_resolution(
            database_url, subject_key, expected_version,
            winner_belief_id=winner, loser_belief_id=loser,
            verdict="contradiction", reasoning=detection.reason, method="rule", confidence=0.9,
        )
        return IngestResult(belief_id, subject_key, claim_text, "resolved_rule", detection.reason)

    # NEEDS_LLM
    arb_input = ArbiterInput(
        subject_key=subject_key,
        claim_a=existing_claim,
        claim_b=ArbiterClaim("B", claim_text, "", authority_tier, confidence, observed_at),
    )
    decision = arbitrate(arb_input)

    winner_id = loser_id = None
    if decision.winner in ("A", "B"):
        winner_id = existing_belief_id if decision.winner == "A" else belief_id
        loser_id = belief_id if decision.winner == "A" else existing_belief_id

    if not decision.needs_human and winner_id is not None:
        commit_resolution(
            database_url, subject_key, expected_version,
            winner_belief_id=winner_id, loser_belief_id=loser_id,
            verdict=decision.verdict, reasoning=decision.reasoning, method="llm", confidence=decision.confidence,
        )
        return IngestResult(belief_id, subject_key, claim_text, "resolved_llm", decision.reasoning)

    commit_contested(
        database_url, existing_belief_id, belief_id, subject_key=subject_key,
        winner_belief_id=winner_id, loser_belief_id=loser_id,
        verdict=decision.verdict, reasoning=decision.reasoning, method="llm", confidence=decision.confidence,
    )
    return IngestResult(belief_id, subject_key, claim_text, "contested", decision.reasoning)


def list_pending_candidates(database_url: str | None = None, limit: int = 50) -> list[uuid.UUID]:
    """Beliefs sitting at status='candidate', oldest first. Used by the
    resolution_worker Lambda's poll loop to find work; see
    resolve_pending_candidate() for why 'candidate' can outlive a single
    ingest() call."""
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM beliefs WHERE status = 'candidate' ORDER BY observed_at ASC LIMIT %s",
                (limit,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _load_pending_candidate(conn, belief_id: uuid.UUID):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.subject_key, b.claim_text, b.embedding, b.confidence, b.observed_at, b.status, s.authority_tier
            FROM beliefs b JOIN sources s ON s.id = b.source_id
            WHERE b.id = %s
            """,
            (belief_id,),
        )
        return cur.fetchone()


def resolve_pending_candidate(belief_id: uuid.UUID, database_url: str | None = None) -> IngestResult:
    """Re-evaluates a belief already sitting at status='candidate' against
    whatever is currently canonical for its subject, and resolves it via the
    same Stage1->Stage2->arbiter->commit path `ingest()`'s conflict branch
    uses - the out-of-band counterpart to ingest()'s synchronous handling,
    for the resolution_worker Lambda's poll loop (see its handler docstring).

    The only way a belief ends up 'candidate' without being resolved
    synchronously by ingest() is PipelineOutcome.NO_CONFLICT (unrelated to
    the current canonical at insert time, so nothing to resolve then) - this
    re-checks it against the *current* canonical, which may have changed
    since. If it's still NO_CONFLICT, this is a no-op (the candidate stays
    'candidate' and will be re-checked on the next poll) - there's no schema
    status for "evaluated, not canonical, not conflicting", and adding one
    is a schema decision flagged in docs/REVIEW_LOG.md rather than made here.
    """
    conn = get_connection(database_url)
    try:
        row = _load_pending_candidate(conn, belief_id)
        if row is None:
            return IngestResult(belief_id, "", "", "not_found")
        subject_key, claim_text, embedding, confidence, observed_at, status, authority_tier = row
        # register_vector() makes psycopg2 hand back a pgvector Vector, not a
        # plain list - unlike ingest()'s embeddings (always fresh from
        # generate_embedding(), never round-tripped through the DB), this is
        # the first path that re-reads an embedding back out of beliefs, so
        # it's the first place this mismatch can surface. detect_conflict()
        # (via evaluate_new_belief) does list(new_embedding), which raises
        # TypeError on a Vector (no __iter__) - normalize here instead.
        if hasattr(embedding, "to_list"):
            embedding = embedding.to_list()
        if status != "candidate":
            return IngestResult(belief_id, subject_key, claim_text, "skipped_not_candidate")

        with conn.cursor() as cur:
            cur.execute("SELECT canonical_belief_id, version, volatility FROM subjects WHERE subject_key = %s", (subject_key,))
            canonical_belief_id, expected_version, volatility = cur.fetchone()

        if canonical_belief_id is None or canonical_belief_id == belief_id:
            # Race: subject had no canonical (or this belief already is it) by
            # the time we got here - promote directly, mirroring ingest()'s
            # NO_CANONICAL branch.
            with conn.cursor() as cur:
                cur.execute("UPDATE beliefs SET status = 'canonical' WHERE id = %s", (belief_id,))
                cur.execute(
                    "UPDATE subjects SET canonical_belief_id = %s, version = version + 1, updated_at = now() WHERE subject_key = %s",
                    (belief_id, subject_key),
                )
            conn.commit()
            return IngestResult(belief_id, subject_key, claim_text, "canonical")

        new_candidate = RuleCandidate(authority_tier=authority_tier, confidence=float(confidence), observed_at=observed_at)
        detection = evaluate_new_belief(conn, subject_key, embedding, new_candidate, volatility)

        if detection.outcome == PipelineOutcome.NO_CONFLICT:
            return IngestResult(belief_id, subject_key, claim_text, "no_conflict")

        if detection.outcome == PipelineOutcome.DUPLICATE:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE beliefs SET status = 'superseded', superseded_by = %s WHERE id = %s",
                    (detection.canonical_belief_id, belief_id),
                )
                cur.execute("UPDATE beliefs SET observed_at = now() WHERE id = %s", (detection.canonical_belief_id,))
            conn.commit()
            return IngestResult(belief_id, subject_key, claim_text, "duplicate", f"merged into existing belief {detection.canonical_belief_id}")

        existing_belief_id = detection.canonical_belief_id
        existing_claim = _load_belief_for_arbiter(conn, existing_belief_id)
    finally:
        conn.close()

    if detection.outcome == PipelineOutcome.RULE_DECIDED:
        if detection.rule_outcome == RuleOutcome.NEW_WINS:
            winner, loser = belief_id, existing_belief_id
        else:
            winner, loser = existing_belief_id, belief_id
        commit_resolution(
            database_url, subject_key, expected_version,
            winner_belief_id=winner, loser_belief_id=loser,
            verdict="contradiction", reasoning=detection.reason, method="rule", confidence=0.9,
        )
        return IngestResult(belief_id, subject_key, claim_text, "resolved_rule", detection.reason)

    # NEEDS_LLM
    arb_input = ArbiterInput(
        subject_key=subject_key,
        claim_a=existing_claim,
        claim_b=ArbiterClaim("B", claim_text, "", authority_tier, float(confidence), observed_at),
    )
    decision = arbitrate(arb_input)

    winner_id = loser_id = None
    if decision.winner in ("A", "B"):
        winner_id = existing_belief_id if decision.winner == "A" else belief_id
        loser_id = belief_id if decision.winner == "A" else existing_belief_id

    if not decision.needs_human and winner_id is not None:
        commit_resolution(
            database_url, subject_key, expected_version,
            winner_belief_id=winner_id, loser_belief_id=loser_id,
            verdict=decision.verdict, reasoning=decision.reasoning, method="llm", confidence=decision.confidence,
        )
        return IngestResult(belief_id, subject_key, claim_text, "resolved_llm", decision.reasoning)

    commit_contested(
        database_url, existing_belief_id, belief_id, subject_key=subject_key,
        winner_belief_id=winner_id, loser_belief_id=loser_id,
        verdict=decision.verdict, reasoning=decision.reasoning, method="llm", confidence=decision.confidence,
    )
    return IngestResult(belief_id, subject_key, claim_text, "contested", decision.reasoning)
