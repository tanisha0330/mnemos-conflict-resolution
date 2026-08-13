"""Applies SQL files in src/schema/migrations/ in order, tracking which have
already run in a schema_migrations table so re-runs are idempotent.

Usage: python -m src.schema.migrate [DATABASE_URL]
"""

import sys
from pathlib import Path

from src.schema.db import get_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_migrations_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _applied_migrations(cur) -> set[str]:
    cur.execute("SELECT filename FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def run_migrations(database_url: str | None = None, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies pending migrations, returns the list of filenames that were applied."""
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            _ensure_migrations_table(cur)
        conn.commit()

        with conn.cursor() as cur:
            already_applied = _applied_migrations(cur)

        applied = []
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in already_applied:
                continue
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            conn.commit()
            applied.append(path.name)
        return applied
    finally:
        conn.close()


if __name__ == "__main__":
    database_url = sys.argv[1] if len(sys.argv) > 1 else None
    applied = run_migrations(database_url)
    if applied:
        print(f"Applied {len(applied)} migration(s):")
        for name in applied:
            print(f"  - {name}")
    else:
        print("No pending migrations.")
