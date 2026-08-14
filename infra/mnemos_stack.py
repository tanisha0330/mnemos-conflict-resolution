"""Mnemos AWS infrastructure: S3 for source artifacts, a Lambda resolution
worker, and 3 Fargate demo agents - all with least-privilege IAM roles, not
broad admin access.

Scope note: this stack is written and validated via `cdk synth` only, not
deployed. See docs/REVIEW_LOG.md Block 4A entry for why (the only AWS
credentials available in this environment are the account root user, and
Fargate services left running would incur unattended cost) and for the exact
steps to deploy and verify reproducibility yourself.
"""

from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

LAMBDA_SRC_DIR = str(Path(__file__).parent / "lambda_src" / "resolution_worker")

# Least-privilege: scope Bedrock access to exactly the two models this
# project uses (from .env), not "bedrock:*" on "*".
BEDROCK_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
BEDROCK_ARBITER_MODEL_ID = "amazon.nova-lite-v1:0"
BEDROCK_ARBITER_FALLBACK_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"

DEMO_AGENTS = ["payment-agent", "support-agent", "fulfillment-agent"]


class MnemosStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- S3: source artifacts, referenced by beliefs.source_id metadata ---
        self.artifacts_bucket = s3.Bucket(
            self, "SourceArtifactsBucket",
            removal_policy=RemovalPolicy.RETAIN,  # provenance evidence - never auto-delete
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
        )

        # --- Secret placeholder for DATABASE_URL (not populated by this stack -
        # deploy-time concern, kept out of source control same as .env) ---
        self.db_secret_arn_param = self.node.try_get_context("database_url_secret_arn") or (
            f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:mnemos/database-url-*"
        )

        # --- Lambda: resolution worker, invoked on a schedule (see handler.py
        # docstring for why this is a poll pattern, not an INSERT trigger) ---
        resolution_worker_role = iam.Role(
            self, "ResolutionWorkerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Least-privilege execution role for the mnemos resolution worker Lambda",
        )
        resolution_worker_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/*"],
        ))
        resolution_worker_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:Converse"],
            resources=[
                f"arn:aws:bedrock:{self.region}::foundation-model/{BEDROCK_EMBEDDING_MODEL_ID}",
                f"arn:aws:bedrock:{self.region}::foundation-model/{BEDROCK_ARBITER_MODEL_ID}",
                f"arn:aws:bedrock:{self.region}::foundation-model/{BEDROCK_ARBITER_FALLBACK_MODEL_ID}",
            ],
        ))
        resolution_worker_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.db_secret_arn_param],
        ))

        self.resolution_worker = lambda_.Function(
            self, "ResolutionWorker",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            # plain zip asset, no Docker bundling - deployment-time TODO:
            # package src/ + its deps as a Lambda layer, see handler.py
            code=lambda_.Code.from_asset(LAMBDA_SRC_DIR),
            role=resolution_worker_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )

        poll_rule = events.Rule(
            self, "ResolutionWorkerScheduleRule",
            schedule=events.Schedule.rate(Duration.minutes(1)),
            description="Polls for unresolved candidate beliefs (outbox pattern - see handler.py)",
        )
        poll_rule.add_target(targets.LambdaFunction(self.resolution_worker))

        # --- Fargate: 3 demo agents ---
        # Public subnets + no NAT gateway: agents only need outbound internet
        # (CockroachDB Cloud, Bedrock), not inbound - avoids NAT gateway cost
        # for a demo deployment. A production deployment would use private
        # subnets + NAT.
        vpc = ec2.Vpc(self, "DemoVpc", max_azs=2, nat_gateways=0)
        cluster = ecs.Cluster(self, "DemoAgentsCluster", vpc=vpc)

        self.agent_services = {}
        for agent_name in DEMO_AGENTS:
            task_role = iam.Role(
                self, f"{agent_name}-task-role",
                assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                description=f"Least-privilege task role for the {agent_name} demo agent",
            )
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/{BEDROCK_EMBEDDING_MODEL_ID}",
                    f"arn:aws:bedrock:{self.region}::foundation-model/{BEDROCK_ARBITER_MODEL_ID}",
                ],
            ))
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[self.db_secret_arn_param],
            ))
            self.artifacts_bucket.grant_read(task_role)  # agents read provenance artifacts, never write

            task_def = ecs.FargateTaskDefinition(
                self, f"{agent_name}-task-def",
                cpu=256, memory_limit_mib=512,
                task_role=task_role,
            )
            task_def.add_container(
                f"{agent_name}-container",
                # Placeholder public image - deployment-time TODO: replace with
                # this repo's actual agent image pushed to ECR. Deliberately
                # not built from a local Dockerfile here (no Docker available
                # in this environment - see docs/REVIEW_LOG.md).
                image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.12-slim"),
                logging=ecs.LogDrivers.aws_logs(stream_prefix=agent_name),
                environment={"AGENT_NAME": agent_name},
            )

            service = ecs.FargateService(
                self, f"{agent_name}-service",
                cluster=cluster,
                task_definition=task_def,
                desired_count=0,  # 0 by default: defined but not running until deliberately scaled up
                assign_public_ip=True,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            )
            self.agent_services[agent_name] = service
