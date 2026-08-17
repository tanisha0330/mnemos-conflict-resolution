# Mnemos AWS infrastructure (CDK)

Defines: an S3 bucket for source artifacts, a Lambda resolution worker (polled via
EventBridge - see `lambda_src/resolution_worker/handler.py` for why this is a poll
pattern rather than a native INSERT trigger), and 3 Fargate task definitions/services
for the demo agents (`desired_count=0` by default - defined but not running until you
deliberately scale up).

**Status: deployed and verified for real** (2026-08-17) - `cdk deploy` -> real Lambda
invoke against the live cluster -> `cdk destroy` -> `cdk deploy` again from scratch,
all done for real, not just `cdk synth`. See docs/REVIEW_LOG.md for the full
checkpoint writeup including every manual step and bug this surfaced. The steps below
are what was *actually* needed, corrected from the original synth-only draft of this
file.

## Prerequisites (all of this was missing on a fresh machine)

1. **AWS CLI or at least boto3 + real credentials.** Neither was present initially.
   `aws configure` (or equivalent env vars) with a scoped IAM identity is what you
   want - see the root-user caveat below.
2. **Node.js + npx** (for `npx aws-cdk`) - no need to `npm install -g aws-cdk`.
3. **Python 3.12 venv with `requirements.txt` installed** (already covered by the
   main repo README) - `pip install aws-cdk-lib constructs` is in there.
4. No Docker needed anywhere in this flow - see the Lambda layer step below.

## Deploy it yourself

```bash
cd infra

# 1. One-time: build the Lambda's dependency layer from real manylinux wheels.
#    No Docker required - every dependency here ships a prebuilt manylinux wheel,
#    so cross-platform `pip install --platform` works even from Windows/macOS.
#    boto3 is deliberately excluded - the Lambda Python 3.12 runtime provides it.
pip install \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --abi cp312 \
  --only-binary=:all: --target lambda_layer/python --upgrade \
  "certifi==2026.7.22" "pgvector==0.5.0" "psycopg2-binary==2.9.12" "python-dotenv==1.2.2" "SQLAlchemy==2.0.52"
# (versions should track requirements.txt at the repo root)

# 2. Create the DATABASE_URL secret this stack's IAM policies reference.
#    The stack grants read access to it but doesn't create/populate it - do this
#    once per account. Value must be the real CockroachDB connection string.
python -c "
import boto3, os
from dotenv import load_dotenv
load_dotenv('../.env')
boto3.client('secretsmanager', region_name='us-east-1').create_secret(
    Name='mnemos/database-url', SecretString=os.environ['DATABASE_URL'])
"

# 3. Bootstrap (one-time per account/region) and deploy.
npx aws-cdk bootstrap aws://ACCOUNT_ID/us-east-1
npx aws-cdk deploy
```

The handler is wired for real (`src.ingestion.pipeline.resolve_pending_candidate`,
not a stub) and `mnemos_stack.py` stages `handler.py` + a real copy of this repo's
`src/` package into `infra/.build/` at synth time - no manual packaging step needed
beyond the layer above. The Fargate containers still use a placeholder public image
(`public.ecr.aws/docker/library/python:3.12-slim`) - replacing that with this repo's
actual agent image, pushed to ECR, is still a real TODO, not done in this pass.

## Real findings from doing this for real

- **Root credentials can deploy, but can't assume the CDK bootstrap roles.**
  Deploying as the AWS account root user works (CloudFormation accepts it), but CDK
  prints `current credentials could not be used to assume
  '...cdk-hnb659fds-file-publishing-role...'` and `'...-deploy-role...'` and silently
  falls back to using the root credentials directly for asset publishing instead of
  the least-privilege bootstrap roles CDK normally uses. Deployment still succeeds,
  but this defeats part of the reason those roles exist. **Use a scoped IAM user/role
  for real deployments** - root working anyway is not an endorsement of using it.
- **A real, non-hypothetical bug in the resolution worker's own logic**, not an infra
  problem: `resolve_pending_candidate()` is the first code path that re-reads an
  embedding back out of `beliefs` with `register_vector()` active, which hands back a
  `pgvector.Vector` object, not a plain `list`. `detect_conflict()` does
  `list(new_embedding)`, which raises `TypeError: 'Vector' object is not iterable` on
  that type. Every real invocation hit this on every pending candidate until fixed
  (`_load_pending_candidate` now calls `.to_list()`). Regression tests added in
  `tests/test_ingestion_pipeline.py`.
- **This bug was invisible from CloudWatch's default view**: the Lambda reported
  `Status: timeout` at exactly its configured function timeout on *every* invocation,
  including ones with zero pending candidates, with no log output at all before the
  fix - looked exactly like a network/connectivity problem (e.g. a CockroachDB Cloud
  IP allowlist blocking Lambda's egress) until staged `print(..., flush=True)`
  statements were added to the handler and redeployed to pinpoint it. It was not a
  networking problem - default (non-VPC) Lambda networking reaches CockroachDB Cloud
  and Secrets Manager fine.
- **A real, pre-existing backlog of 50 `status='candidate'` beliefs** was sitting in
  the live cluster before this Lambda ever ran - leftovers from
  `PipelineOutcome.NO_CONFLICT` (see `resolve_pending_candidate()`'s docstring: that's
  the one outcome `ingest()` never resolves further, by design, and there's currently
  no belief status for "evaluated, still not conflicting" - a still-NO_CONFLICT
  candidate stays `'candidate'` and gets re-checked every poll). Not a bug introduced
  by this deploy, but this deploy is what surfaced it.
- **Real per-candidate latency from Lambda is much higher than from a local dev
  machine** - roughly 1s/candidate locally vs. ~5s/candidate from Lambda (a fresh
  `psycopg2.connect()` with `sslmode=verify-full` per DB call, no connection reuse
  across candidates). 50 candidates at that rate would not reliably finish inside the
  original 60s function timeout. Raised to `Duration.seconds(120)` and capped
  `POLL_LIMIT=20` per invocation - safe either way, since
  `resolve_pending_candidate()` only ever touches one belief row per iteration, so a
  mid-batch timeout just defers the rest to the next scheduled poll instead of
  producing a partial or incorrect write.
- **The concurrency-safety mechanism fired for real, not just in unit tests**: a
  manual invoke overlapping with the EventBridge schedule's own concurrent invocation
  produced a real `StaleResolutionError` ("subject version changed since the decision
  was made") on one candidate - CLAUDE.md's optimistic-version-check design working
  exactly as intended under genuine concurrent access, not a failure.
- **`cdk destroy` doesn't remove everything, by design**: the S3 bucket
  (`RemovalPolicy.RETAIN`, deliberate - provenance artifacts should never auto-delete)
  and the three Fargate containers' CloudWatch log groups are left behind
  (`DELETE_SKIPPED` in the CFN event log). Expected, not a teardown bug - if you want
  a fully clean account, delete those manually after `cdk destroy`.
- **The CDK CLI's own self-reported "Deployment time" can be bogus.** One redeploy
  printed `Deployment time: 6304.78s` (~105 minutes) that did not happen - the actual
  CloudFormation `StackEvents` timestamps (confirmed directly via
  `describe_stacks`/event log, not assumed) show the real deploy took about 2 minutes,
  consistent with every other run. Trust CloudFormation's own timestamps over the
  CLI's printed summary if the two ever disagree.

## Operational note for anyone running this near a live demo

The EventBridge rule polls **every 1 minute** and will keep mutating real rows in
whatever database `mnemos/database-url` points at for as long as the stack is
deployed - including any shared/demo dataset. If you don't want that running
unattended before a live demo, either scale the schedule down
(`events.Schedule.rate(Duration.hours(...))`), disable the rule
(`aws events disable-rule`), or `cdk destroy` when not actively verifying it.

## Verifying reproducibility

Already done once for real (2026-08-17): `cdk deploy` (55 resources, ~114s) -> real
Lambda invoke confirmed against the live cluster -> `cdk destroy` (51 resources
deleted, S3 bucket + log groups retained by design) -> `cdk deploy` again (identical
55 resources, ~2 min per CloudFormation's own timestamps). To repeat:

```bash
npx aws-cdk deploy
# confirm it works as expected, then:
npx aws-cdk destroy
npx aws-cdk deploy
# confirm it comes back up identically
```
