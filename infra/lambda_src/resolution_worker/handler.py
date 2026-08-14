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
"""

import json


def handler(event, context):
    # Deployment-time TODO: import and call src.resolution.pipeline /
    # src.resolution.commit here once dependency packaging (a Lambda layer
    # bundling this repo's src/ package + its requirements) is set up. Not
    # wired up in this IaC-only pass - see docs/REVIEW_LOG.md Block 4A entry.
    print(f"resolution worker invoked, event={json.dumps(event)}")
    return {"statusCode": 200, "body": "ok"}
