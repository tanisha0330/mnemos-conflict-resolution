# Mnemos AWS infrastructure (CDK)

Defines: an S3 bucket for source artifacts, a Lambda resolution worker (polled via
EventBridge - see `lambda_src/resolution_worker/handler.py` for why this is a poll
pattern rather than a native INSERT trigger), and 3 Fargate task definitions/services
for the demo agents (`desired_count=0` by default - defined but not running until you
deliberately scale up).

**Status: validated via `cdk synth` only, not deployed.** See `docs/REVIEW_LOG.md`
(Block 4A entry) for why - the only AWS credentials available in the environment this
was built in are the account root user, and live Fargate services would incur
unattended cost without a deliberate decision to accept that.

## Deploy it yourself

```bash
cd infra
pip install aws-cdk-lib constructs   # already in the project's requirements.txt
npx aws-cdk bootstrap                # one-time per account/region
npx aws-cdk deploy
```

You'll need real AWS credentials with permission to create the resources above
(ideally a scoped IAM role/user, not root - the deployed resources' own roles are
least-privilege, but the *deploying* principal's permissions are a separate concern
this stack doesn't control).

**Before deploying for real:**
1. Create the `DATABASE_URL` secret this stack's IAM policies reference (`mnemos/database-url` in Secrets Manager) - the stack grants read access to it but doesn't create or populate it.
2. Package `src/` + its dependencies as a Lambda layer (or into the function's own zip) and wire the handler to actually call `src.resolution.pipeline`/`src.resolution.commit` - the current handler is a stub (see its docstring).
3. Replace the Fargate containers' placeholder image (`public.ecr.aws/docker/library/python:3.12-slim`) with this repo's actual agent image, pushed to ECR.

## Verifying reproducibility (the Block 4A checkpoint I couldn't do myself)

The build plan's own checkpoint for this block is: *"Delete and redeploy from scratch
once to confirm the IaC is genuinely reproducible - this is exactly what judges test."*
I couldn't do this under the no-live-deploy scope. To verify it yourself:

```bash
npx aws-cdk deploy
# confirm it works as expected, then:
npx aws-cdk destroy
npx aws-cdk deploy
# confirm it comes back up identically
```
