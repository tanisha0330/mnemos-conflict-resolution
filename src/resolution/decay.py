"""Decay policy: superseded beliefs older than a configurable TTL get
archived=true. Never deletes anything ("claims not facts" - the audit trail
stays intact); archived just means deprioritized from default retrieval.
"""

from datetime import datetime, timedelta, timezone

DEFAULT_TTL = timedelta(days=90)


def apply_decay(conn, ttl: timedelta = DEFAULT_TTL) -> int:
    """Archives superseded beliefs whose observed_at is older than `ttl`.
    Returns the number of beliefs archived."""
    cutoff = datetime.now(timezone.utc) - ttl
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE beliefs SET archived = true WHERE status = 'superseded' AND archived = false AND observed_at < %s",
            (cutoff,),
        )
        count = cur.rowcount
    conn.commit()
    return count
