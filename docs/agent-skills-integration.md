# CockroachDB Agent Skills Repo integration

**What it is:** [`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills) — Cockroach Labs' official open-source collection of machine-executable "Agent Skills" (the same `SKILL.md`-with-frontmatter format Claude Code itself uses), covering onboarding, schema/query design, performance, resilience, observability, security, and cost management. Model-agnostic; portable across Claude Code, Cursor, and 70+ other agent frameworks via the [Agent Skills Specification](https://agentskills.io).

**How it was installed:** via the standard installer, `npx skills add cockroachlabs/cockroachdb-skills --skill <name> -a claude-code`, which clones the repo, resolves the requested skills, runs them through Snyk/Socket security scanning, and copies each one into `.claude/skills/<name>/`. This is a real, working install — not a README mention — verifiable in this repo at `.claude/skills/`.

**Why these 7 (of 34 available):** the repo skews heavily toward multi-region topology, CMEK/SSO/compliance, and self-hosted cluster ops — none relevant to a single small CockroachDB Cloud Serverless cluster. The ones actually applicable to this project's schema and concurrency work:

| Skill | Why it's relevant here |
|---|---|
| `designing-application-transactions` | Directly informs Block 2B's serializable-transaction + 40001-retry commit logic — the skill's own retry/backoff and connection-pooling guidance is what that block implements. |
| `benchmarking-transaction-patterns` | Directly informs Block 2C's concurrency proof methodology (fair test design, contention analysis). |
| `cockroachdb-sql` | Schema/query design and anti-pattern rules (see security note below). |
| `profiling-statement-fingerprints` | Diagnosing slow queries in the stress test (this block) and concurrency test (Block 2C) via `crdb_internal.statement_statistics`, without needing DB Console access. |
| `profiling-transaction-fingerprints` | Diagnosing retry/contention patterns the same way, via `crdb_internal.transaction_statistics` — directly useful for reading Block 2C's results. |
| `auditing-table-statistics` | Checking optimizer stats aren't stale after the stress test's bulk insert, which would otherwise skew later query-plan interpretation. |
| `setting-up-local-cluster` | Downloads and runs a local CockroachDB cluster from the **official binary directly** (no Docker) — a candidate for Block 4C's local-cluster failure demo, given this build has no Docker available. |

**Security note (worth flagging, not burying):** the installer's built-in scan (Snyk/Socket) rated `cockroachdb-sql` **High Risk**, while all six other skills came back Safe/Low or Med. I read the full `SKILL.md` before deciding whether to keep it: there's no bundled executable code (just markdown + a `references/` folder of rule files, same shape as every other skill here), so the High Risk rating is behavioral, not code-injection — the skill's own instructions tell the invoking agent to autonomously discover a connection string (from the prompt, `$COCKROACH_URL`, or an MCP server) and run `cockroach sql --url ... -e "SQL"` shell commands against it, including a mandatory `EXPLAIN` on every generated query. That's meaningfully higher blast-radius than the other skills, which are purely advisory.

**Decision:** kept it installed, but treated as reference material only in this project — I read its `references/cockroachdb-rules/` anti-pattern rules directly (e.g. confirmed our Block 1A schema already follows its UUID-primary-key-over-sequential-ID rule) rather than letting it autonomously execute shell commands against `DATABASE_URL`. If this project were handed to a team that invokes skills more automatically, this is the one to review first.

**Verifiable state:** `.claude/skills/{designing-application-transactions,benchmarking-transaction-patterns,cockroachdb-sql,profiling-statement-fingerprints,profiling-transaction-fingerprints,auditing-table-statistics,setting-up-local-cluster}/SKILL.md`, committed to this repo (an exception was carved into `.gitignore` for `.claude/skills/` specifically, since the rest of `.claude/` is local tool state).
