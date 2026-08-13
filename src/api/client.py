"""Clean Python client API for mnemos, matching common memory-library
conventions: add / search / get_all / history / as_of.

Canonical-only by default in search() and get_all() - CLAUDE.md: "the key
behavioral difference from Mem0-style APIs - don't let it regress." Pass
include_superseded=True to see everything (candidate/canonical/superseded/
contested), not just superseded specifically - the flag name matches the
literal spec text, but its effect is "no status filter."
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from src.ingestion.embeddings import generate_embedding
from src.ingestion.pipeline import IngestResult, ingest
from src.schema.db import get_connection

_BELIEF_COLUMNS = """
    b.id, b.subject_key, b.claim_text, b.agent_id, s.name, s.authority_tier,
    b.confidence, b.observed_at, b.status, b.superseded_by, b.created_at, b.archived
"""


@dataclass(frozen=True)
class Belief:
    id: uuid.UUID
    subject_key: str
    claim_text: str
    agent_id: str
    source_name: str
    authority_tier: int
    confidence: float
    observed_at: datetime
    status: str
    superseded_by: uuid.UUID | None
    created_at: datetime
    archived: bool


@dataclass(frozen=True)
class ResolutionRecord:
    id: uuid.UUID
    winner_belief_id: uuid.UUID
    loser_belief_id: uuid.UUID
    verdict: str
    reasoning: str
    method: str
    confidence: float
    resolved_at: datetime


@dataclass(frozen=True)
class HistoryEntry:
    belief: Belief
    resolution: ResolutionRecord | None  # the resolution that involved this belief, if any


@dataclass(frozen=True)
class AsOfResult:
    """Clean, demo-ready snapshot of a subject at a point in time: the
    canonical belief then, plus every other belief that existed at that time
    with the resolution reasoning that explains why it isn't canonical."""
    subject_key: str
    as_of: datetime
    version: int | None
    canonical: Belief | None
    superseded: list[HistoryEntry]

    def pretty(self) -> str:
        lines = [f"Subject: {self.subject_key}", f"As of:   {self.as_of.isoformat()}"]
        if self.canonical:
            lines.append(
                f"Canonical: \"{self.canonical.claim_text}\" "
                f"(source={self.canonical.source_name}, confidence={self.canonical.confidence})"
            )
        else:
            lines.append("Canonical: (none yet at this time)")
        if self.superseded:
            lines.append("Other claims at this time:")
            for entry in self.superseded:
                reasoning = f" - {entry.resolution.reasoning}" if entry.resolution else ""
                lines.append(f"  - [{entry.belief.status}] \"{entry.belief.claim_text}\" (source={entry.belief.source_name}){reasoning}")
        return "\n".join(lines)


def _row_to_belief(row) -> Belief:
    return Belief(*row)


class MnemosClient:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url

    def add(self, text: str, agent_id: str, source_id: uuid.UUID, **kwargs) -> IngestResult:
        return ingest(text, agent_id, source_id, database_url=self.database_url, **kwargs)

    def search(
        self, query: str, subject_key: str | None = None, limit: int = 10,
        include_superseded: bool = False, include_archived: bool = False,
    ) -> list[Belief]:
        embedding = generate_embedding(query)
        clauses = []
        params: list = []
        if not include_superseded:
            clauses.append("b.status = 'canonical'")
        elif not include_archived:
            clauses.append("b.archived = false")
        if subject_key is not None:
            clauses.append("b.subject_key = %s")
            params.append(subject_key)
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        conn = get_connection(self.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_BELIEF_COLUMNS}
                    FROM beliefs b JOIN sources s ON s.id = b.source_id
                    {where_sql}
                    ORDER BY b.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    params + [embedding, limit],
                )
                return [_row_to_belief(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_all(self, subject_key: str, include_superseded: bool = False, include_archived: bool = False) -> list[Belief]:
        clauses = ["b.subject_key = %s"]
        params: list = [subject_key]
        if not include_superseded:
            clauses.append("b.status = 'canonical'")
        elif not include_archived:
            clauses.append("b.archived = false")
        where_sql = "WHERE " + " AND ".join(clauses)

        conn = get_connection(self.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_BELIEF_COLUMNS}
                    FROM beliefs b JOIN sources s ON s.id = b.source_id
                    {where_sql}
                    ORDER BY b.observed_at DESC
                    """,
                    params,
                )
                return [_row_to_belief(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def history(self, subject_key: str) -> list[HistoryEntry]:
        """Full belief history for a subject, including superseded/contested
        beliefs, each annotated with the resolution that decided its fate
        (if any) - reasoning included, not just the status flip."""
        conn = get_connection(self.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_BELIEF_COLUMNS}
                    FROM beliefs b JOIN sources s ON s.id = b.source_id
                    WHERE b.subject_key = %s
                    ORDER BY b.observed_at ASC
                    """,
                    (subject_key,),
                )
                beliefs = [_row_to_belief(row) for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT id, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence, resolved_at
                    FROM resolutions
                    WHERE subject_key = %s
                    ORDER BY resolved_at ASC
                    """,
                    (subject_key,),
                )
                resolutions = [ResolutionRecord(*row) for row in cur.fetchall()]
        finally:
            conn.close()

        entries = []
        for belief in beliefs:
            matching = next(
                (r for r in resolutions if belief.id in (r.winner_belief_id, r.loser_belief_id)),
                None,
            )
            entries.append(HistoryEntry(belief=belief, resolution=matching))
        return entries

    def as_of(self, subject_key: str, timestamp: datetime) -> AsOfResult:
        """Clean, demo-ready snapshot of a subject at a point in the past, via
        AS OF SYSTEM TIME: the canonical belief then, plus every other belief
        that existed at that time annotated with the resolution reasoning
        that explains it.

        Requires autocommit (a fresh, standalone statement per query) -
        CockroachDB enforces a consistent historical timestamp across all
        statements in one transaction, which a shared connection's implicit
        transaction would otherwise violate on the second query."""
        ts_literal = timestamp.astimezone().isoformat()
        conn = get_connection(self.database_url, autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT canonical_belief_id, version FROM subjects AS OF SYSTEM TIME '{ts_literal}' WHERE subject_key = %s",
                    (subject_key,),
                )
                row = cur.fetchone()
                if row is None:
                    return AsOfResult(subject_key, timestamp, None, None, [])
                canonical_belief_id, version = row

                cur.execute(
                    f"""
                    SELECT {_BELIEF_COLUMNS}
                    FROM beliefs b JOIN sources s ON s.id = b.source_id
                    AS OF SYSTEM TIME '{ts_literal}'
                    WHERE b.subject_key = %s
                    ORDER BY b.observed_at ASC
                    """,
                    (subject_key,),
                )
                all_beliefs = [_row_to_belief(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT id, winner_belief_id, loser_belief_id, verdict, reasoning, method, confidence, resolved_at
                    FROM resolutions AS OF SYSTEM TIME '{ts_literal}'
                    WHERE subject_key = %s
                    """,
                    (subject_key,),
                )
                resolutions = [ResolutionRecord(*r) for r in cur.fetchall()]
        finally:
            conn.close()

        canonical = next((b for b in all_beliefs if b.id == canonical_belief_id), None)
        superseded = [
            HistoryEntry(
                belief=b,
                resolution=next((r for r in resolutions if b.id in (r.winner_belief_id, r.loser_belief_id)), None),
            )
            for b in all_beliefs
            if b.id != canonical_belief_id
        ]
        return AsOfResult(subject_key, timestamp, version, canonical, superseded)
