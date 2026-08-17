"""Resolution worker Lambda handler.

Design note on the trigger mechanism: the build spec calls for a Lambda
"triggered by new belief inserts", but CockroachDB (an external database, not
an AWS service) has no native AWS event source to trigger a Lambda directly
on INSERT - there's no CockroachDB-to-EventBridge/SNS integration. This is
implemented as an outbox/poll pattern instead: EventBridge invokes this
Lambda on a schedule (see mnemos_stack.py), and the handler queries for
'candidate' beliefs that haven't been evaluated yet and runs the real
Stage1->Stage2->arbiter->commit pipeline (src/resolution + src/ingestion)
against each one. A production version would likely use CockroachDB
changefeeds (CDC) into a Kafka/webhook sink that posts to an SQS queue
Lambda actually subscribes to, for near-real-time triggering instead of
polling - out of scope for this IaC pass, noted for anyone deploying this
for real.

Most beliefs are resolved synchronously inside src.ingestion.pipeline.ingest()
at write time - this worker exists for the one path ingest() leaves at
status='candidate' without resolving further (PipelineOutcome.NO_CONFLICT:
not conflicting with whatever was canonical *at insert time*), re-checking
each against whatever is canonical *now*. See
src.ingestion.pipeline.resolve_pending_candidate() for the full reasoning,
including the known limitation that a belief which is still NO_CONFLICT on
recheck has no way to be marked "evaluated" and will be picked up again on
the next poll - flagged there and in docs/REVIEW_LOG.md rather than solved
by inventing a new belief status under deploy-time pressure.

DATABASE_URL is not passed as a plain Lambda environment variable (it would
be visible in the Lambda console/API to anyone with lambda:GetFunction) -
this handler fetches it once per invocation from Secrets Manager, the same
secret mnemos_stack.py's IAM policy already scopes read access to
(DB_SECRET_ID / mnemos/database-url).
"""

import json
import os

import boto3

DB_SECRET_ID = os.environ.get("DB_SECRET_ID", "mnemos/database-url")
POLL_LIMIT = int(os.environ.get("POLL_LIMIT", "50"))

_secrets_client = boto3.client("secretsmanager")


def _get_database_url() -> str:
    return _secrets_client.get_secret_value(SecretId=DB_SECRET_ID)["SecretString"]


def handler(event, context):
    print("STAGE: handler start", flush=True)
    from src.ingestion.pipeline import list_pending_candidates, resolve_pending_candidate
    print("STAGE: imports done", flush=True)

    database_url = _get_database_url()
    print("STAGE: got database_url from secrets manager", flush=True)

    pending = list_pending_candidates(database_url, limit=POLL_LIMIT)
    print(f"STAGE: list_pending_candidates done, pending={len(pending)}", flush=True)
    results = []
    for belief_id in pending:
        try:
            result = resolve_pending_candidate(belief_id, database_url=database_url)
            print(f"STAGE: resolved {belief_id} -> {result.outcome}", flush=True)
            results.append({"belief_id": str(belief_id), "outcome": result.outcome})
        except Exception as exc:  # noqa: BLE001 - one bad candidate shouldn't sink the batch
            print(f"STAGE: error resolving {belief_id}: {exc}", flush=True)
            results.append({"belief_id": str(belief_id), "outcome": "error", "detail": str(exc)})

    print(f"resolution worker invoked, event={json.dumps(event)}, "
          f"pending={len(pending)}, results={json.dumps(results)}", flush=True)
    return {"statusCode": 200, "body": json.dumps({"pending": len(pending), "results": results})}
