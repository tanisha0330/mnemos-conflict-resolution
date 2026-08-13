"""Naive-baseline comparison for the concurrency proof (Block 2C).

The interesting, realistic bug here isn't "no transaction at all" - that's
close to a strawman; every team wraps reads+writes in a transaction. The
actual common pitfall is relying on a database's *default* isolation level
without thinking about it: Postgres defaults to READ COMMITTED, which does
NOT protect a read-then-write against a concurrent read-then-write on the
same row - both transactions can read the same value and both commit,
silently discarding one of the updates. CockroachDB's only isolation level
is SERIALIZABLE (by default), so the identical code either succeeds
correctly or fails LOUDLY with a retryable error - it can never silently
lose an update the way Postgres's default does.

Same literal Python logic (read counter, sleep briefly to simulate real
app-layer work between read and write, write counter+1, all inside one
BEGIN/COMMIT transaction, no retry logic) is run against:
  1. local PostgreSQL (READ COMMITTED default)          -> expect real lost updates
  2. CockroachDB Cloud, this project's real cluster (SERIALIZABLE default) -> expect
     either correct increments or loud 40001 errors, never silent loss

Usage: python -m scripts.naive_baseline <postgres_dsn> [cockroachdb_url]
"""

import random
import sys
import threading
import time

import psycopg2
import psycopg2.errors

SLEEP_BETWEEN_READ_AND_WRITE = 0.02  # simulates real app-layer work between read and write


def _reset_counter(dsn: str, **connect_kwargs):
    conn = psycopg2.connect(dsn, **connect_kwargs)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS naive_comparison_counter")
            cur.execute("CREATE TABLE naive_comparison_counter (id INT PRIMARY KEY, value INT NOT NULL)")
            cur.execute("INSERT INTO naive_comparison_counter (id, value) VALUES (1, 0)")
    finally:
        conn.close()


def _naive_increment(dsn: str, results: list, index: int, connect_kwargs: dict):
    """The naive pattern: BEGIN (implicit, autocommit=False) -> SELECT -> sleep
    (simulating app logic) -> UPDATE -> COMMIT. No retry logic. Relies entirely
    on whatever the database's default isolation level happens to do."""
    conn = psycopg2.connect(dsn, **connect_kwargs)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM naive_comparison_counter WHERE id = 1")
            current = cur.fetchone()[0]
        time.sleep(SLEEP_BETWEEN_READ_AND_WRITE + random.uniform(0, 0.01))
        with conn.cursor() as cur:
            cur.execute("UPDATE naive_comparison_counter SET value = %s WHERE id = 1", (current + 1,))
        conn.commit()
        results[index] = "committed"
    except Exception as exc:  # noqa: BLE001 - every writer must be accounted for
        conn.rollback()
        results[index] = f"error: {exc.__class__.__name__}"
    finally:
        conn.close()


def run_naive_test(dsn: str, n_writers: int, **connect_kwargs) -> dict:
    _reset_counter(dsn, **connect_kwargs)

    results = [None] * n_writers
    barrier = threading.Barrier(n_writers)

    def synced(i):
        barrier.wait()
        _naive_increment(dsn, results, i, connect_kwargs)

    start = time.perf_counter()
    threads = [threading.Thread(target=synced, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    assert all(r is not None for r in results), "a writer vanished without recording an outcome"
    committed = sum(1 for r in results if r == "committed")
    errored = n_writers - committed

    conn = psycopg2.connect(dsn, **connect_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM naive_comparison_counter WHERE id = 1")
            final_value = cur.fetchone()[0]
    finally:
        conn.close()

    return {
        "n_writers": n_writers,
        "elapsed_seconds": round(elapsed, 2),
        "committed_transactions": committed,
        "errored_transactions": errored,
        "final_counter_value": final_value,
        "expected_if_zero_lost": committed,  # each successfully-committed txn should have added exactly 1
        "lost_updates": committed - final_value,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.naive_baseline <postgres_dsn> [cockroachdb_url]")
        sys.exit(1)

    postgres_dsn = sys.argv[1]
    print("=== naive read-then-write, PostgreSQL (READ COMMITTED default) ===")
    for n in (50, 200):
        report = run_naive_test(postgres_dsn, n)
        for k, v in report.items():
            print(f"  {k}: {v}")
        print()

    if len(sys.argv) > 2:
        import certifi

        cockroach_url = sys.argv[2]
        print("=== identical naive code, CockroachDB (SERIALIZABLE default, no retry) ===")
        for n in (50, 200):
            report = run_naive_test(cockroach_url, n, sslrootcert=certifi.where())
            for k, v in report.items():
                print(f"  {k}: {v}")
            print()
