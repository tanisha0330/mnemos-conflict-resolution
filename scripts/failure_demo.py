"""Failure demo: a genuine local 3-node CockroachDB cluster (official binary,
no Docker - see docs/failure-resilience.md for exactly how it was set up),
running a continuous read/write workload that round-robins across all 3
nodes' SQL addresses, while one node is killed mid-flight. Demonstrates
reads/writes continuing without downtime.

Not run against this project's real Cloud Serverless cluster: Serverless is
multi-tenant and doesn't expose node-level control or CockroachDB Advanced's
Cloud Console failure-simulation feature (verified directly - see
docs/REVIEW_LOG.md Block 4C entry for how). A local cluster is the only way
to genuinely demonstrate this without Docker.

Usage: python -m scripts.failure_demo
"""

import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

NODE_SQL_PORTS = [27257, 27258, 27259]
NODE_TO_KILL_SQL_PORT = 27259  # node 3
KILL_AFTER_SECONDS = 5
TOTAL_DURATION_SECONDS = 20


def _connect(port: int):
    conn = psycopg2.connect(f"postgresql://root@localhost:{port}/defaultdb?sslmode=disable", connect_timeout=3)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    return conn


def _setup(subject_key: str):
    conn = _connect(NODE_SQL_PORTS[0])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            source_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, 3, 'failure demo')",
                (source_id, f"failure-demo-src-{uuid.uuid4().hex[:6]}"),
            )
            cur.execute(
                "INSERT INTO subjects (subject_key, canonical_belief_id, version, volatility, updated_at) "
                "VALUES (%s, NULL, 1, 'stable', now())",
                (subject_key,),
            )
    finally:
        conn.close()
    return source_id


def _workload_loop(subject_key, source_id, stop_event, results, lock):
    i = 0
    while not stop_event.is_set():
        i += 1
        port = NODE_SQL_PORTS[i % len(NODE_SQL_PORTS)]
        t0 = time.perf_counter()
        try:
            conn = _connect(port)
            conn.autocommit = True
            with conn.cursor() as cur:
                belief_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO beliefs (id, subject_key, claim_text, agent_id, source_id, confidence, observed_at, status)
                    VALUES (%s, %s, %s, 'failure-demo-agent', %s, 0.8, now(), 'candidate')
                    """,
                    (belief_id, subject_key, f"claim {i}", source_id),
                )
                cur.execute("SELECT count(*) FROM beliefs WHERE subject_key = %s", (subject_key,))
                count = cur.fetchone()[0]
            conn.close()
            elapsed = time.perf_counter() - t0
            with lock:
                results.append((datetime.now(timezone.utc), port, "ok", i, count, round(elapsed, 3)))
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            with lock:
                results.append((datetime.now(timezone.utc), port, f"error: {exc.__class__.__name__}", i, None, round(elapsed, 3)))
        time.sleep(0.2)


def _find_node_pid(sql_port: int) -> int | None:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"Name='cockroach.exe'\" | "
         f"Where-Object {{ $_.CommandLine -match 'sql-addr=localhost:{sql_port}' }}).ProcessId"],
        capture_output=True, text=True,
    )
    pid = out.stdout.strip()
    return int(pid) if pid.isdigit() else None


def _kill_node(pid: int):
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"], capture_output=True)


def run_failure_demo() -> dict:
    subject_key = f"failure-demo:{uuid.uuid4()}"
    source_id = _setup(subject_key)

    results = []
    lock = threading.Lock()
    stop_event = threading.Event()
    worker = threading.Thread(target=_workload_loop, args=(subject_key, source_id, stop_event, results, lock))
    worker.start()

    time.sleep(KILL_AFTER_SECONDS)
    pid = _find_node_pid(NODE_TO_KILL_SQL_PORT)
    kill_time = datetime.now(timezone.utc)
    killed = False
    if pid:
        _kill_node(pid)
        killed = True
        print(f"Killed node 3 (SQL port {NODE_TO_KILL_SQL_PORT}, PID {pid}) at {kill_time.isoformat()}")
    else:
        print("WARNING: could not find node 3's PID - node not killed, this run proves nothing")

    time.sleep(TOTAL_DURATION_SECONDS - KILL_AFTER_SECONDS)
    stop_event.set()
    worker.join()

    before = [r for r in results if r[0] < kill_time]
    after = [r for r in results if r[0] >= kill_time]
    before_ok = sum(1 for r in before if r[2] == "ok")
    after_ok = sum(1 for r in after if r[2] == "ok")
    after_errors = [r for r in after if r[2] != "ok"]

    return {
        "subject_key": subject_key,
        "killed": killed,
        "kill_time": kill_time,
        "total_ops": len(results),
        "before_kill_ops": len(before),
        "before_kill_ok": before_ok,
        "after_kill_ops": len(after),
        "after_kill_ok": after_ok,
        "after_kill_errors": len(after_errors),
        "results": results,
    }


if __name__ == "__main__":
    report = run_failure_demo()
    print()
    print(f"subject_key: {report['subject_key']}")
    print(f"total ops: {report['total_ops']}")
    print(f"before kill: {report['before_kill_ok']}/{report['before_kill_ops']} ok")
    print(f"after kill:  {report['after_kill_ok']}/{report['after_kill_ops']} ok ({report['after_kill_errors']} errors, expected: some, from the ~1/3 of ops hitting the dead node)")
    print()
    print("timeline (timestamp, node_port, status, op#, running_count, elapsed_s):")
    for r in report["results"]:
        print(f"  {r[0].isoformat()}  port={r[1]}  {r[2]:20s}  op={r[3]:3d}  count={r[4]}  {r[5]}s")
