# Failure resilience: node failure without memory downtime

## Why this isn't run against the production Cloud cluster

This project's real database is a **CockroachDB Cloud Serverless** cluster. Checked directly (not assumed): `SELECT * FROM crdb_internal.gossip_nodes` returns `Access to crdb_internal and system is restricted` - the exact restriction message for Serverless's multi-tenant architecture, which hides node topology from users entirely. Separately, CockroachDB Cloud does have a real failure-simulation feature (a fault-tolerance demo in the Cloud Console), but it's only available for **CockroachDB Advanced** clusters with 3+ nodes - not Serverless. Neither path is available on this project's actual cluster.

With no Docker available either (this session), the only genuine way to demonstrate a real node failure is a **local multi-node cluster using the official `cockroach` binary directly** - no Docker, no VM, just the binary Cockroach Labs ships. Set up via the `setting-up-local-cluster` Agent Skill installed in Block 1C.

## Setup

```bash
# 3 nodes, official Windows binary, insecure mode (local dev only)
cockroach start --insecure --listen-addr=localhost:27357 --sql-addr=localhost:27257 \
  --http-addr=localhost:28080 --store=<data>/node1 --log-dir=<logs>/node1 \
  --join=localhost:27357,localhost:27358,localhost:27359
# ...same for node2 (ports 27358/27258/28081) and node3 (27359/27259/28082)

cockroach init --insecure --host=localhost:27357
```

Note: the skill's documented `--background` flag isn't supported on Windows builds - each node has to run as its own background process instead.

Schema applied with the project's own migration runner: `python -m src.schema.migrate "postgresql://root@localhost:27257/defaultdb?sslmode=disable"`.

## The demo: `scripts/failure_demo.py`

A continuous read/write workload round-robins across all 3 nodes' SQL addresses (a new short-lived connection per operation, simulating a real client retrying against different endpoints). After 5 seconds, node 3 is killed (`Stop-Process -Force` on its PID, found via its command line). The workload keeps running for 20 seconds total.

## Results - watched live, twice, per the checkpoint

**Run 1:**
```
Killed node 3 (SQL port 27259, PID 3540) at 2026-08-14T07:45:07
before kill: 2/2 ok
after kill:  4/5 ok (1 error - the one operation that tried to connect directly to the now-dead node's address)
```

**Run 2** (node 3 restarted and rejoined between runs):
```
Killed node 3 (SQL port 27259, PID 14996) at 2026-08-14T07:46:20
before kill: 2/2 ok
after kill:  4/5 ok (1 error, same pattern)
```

Both runs: identical shape. The cluster itself never went down - writes and reads via node 1 and node 2 succeeded throughout, including immediately after the kill. The only failures were the individual operations that specifically tried to open a *new* connection to the dead node's own address (expected client-side behavior for anyone talking directly to a specific node rather than through a load balancer) - and those recovered on the very next operation, routed to a surviving node.

One real observation, not smoothed over: the first post-kill operation on the surviving nodes took longer than usual (5.5s and 5.5s in the two runs, vs. ~2s normally) - consistent with Raft leadership/range-lease transfer happening in the background for ranges node 3 held leases on. Not downtime, but a real, measurable latency blip worth knowing about rather than hiding.
