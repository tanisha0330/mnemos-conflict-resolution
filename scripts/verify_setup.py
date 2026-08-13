"""
Verifies that the local environment is ready for mnemos development:
  1. CockroachDB is reachable via DATABASE_URL
  2. AWS credentials are configured (STS get-caller-identity)
  3. Bedrock model access is granted for the embedding/arbiter models

Run with: python scripts/verify_setup.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Windows terminals often default to a legacy codepage (cp1252) that can't
# encode the ✅/❌ status icons; force UTF-8 so this script works cross-platform.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CHECK = "✅"
CROSS = "❌"

results = []


def report(name: str, ok: bool, detail: str, hint: str = "") -> None:
    icon = CHECK if ok else CROSS
    print(f"{icon} {name}: {detail}")
    if not ok and hint:
        print(f"   fix: {hint}")
    results.append(ok)


def check_database() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        report(
            "CockroachDB",
            False,
            "DATABASE_URL is not set",
            "copy .env.example to .env and fill in DATABASE_URL from the CockroachDB Cloud console",
        )
        return

    try:
        import psycopg2
    except ImportError:
        report(
            "CockroachDB",
            False,
            "psycopg2 is not installed",
            "run: pip install -r requirements.txt",
        )
        return

    # CockroachDB Cloud certs are publicly trusted. psycopg2-binary's bundled
    # libpq doesn't reliably read the Windows cert store via sslrootcert=system,
    # so point it at certifi's CA bundle instead of requiring a downloaded CA file.
    connect_kwargs = {}
    if "sslrootcert" not in database_url:
        import certifi

        connect_kwargs["sslrootcert"] = certifi.where()

    try:
        conn = psycopg2.connect(database_url, connect_timeout=10, **connect_kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
            report("CockroachDB", True, version)
        finally:
            conn.close()
    except Exception as exc:
        report(
            "CockroachDB",
            False,
            f"connection failed: {exc}",
            "check DATABASE_URL in .env (host, port, sslmode) and that your IP is allowlisted in CockroachDB Cloud",
        )


def check_aws_identity() -> None:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        report(
            "AWS credentials",
            False,
            "boto3 is not installed",
            "run: pip install -r requirements.txt",
        )
        return

    try:
        sts = boto3.client("sts", region_name=os.environ.get("AWS_REGION"))
        identity = sts.get_caller_identity()
        report("AWS credentials", True, f"account {identity['Account']} (arn: {identity['Arn']})")
    except NoCredentialsError:
        report(
            "AWS credentials",
            False,
            "no credentials found",
            "run: aws configure  (or aws sso login if using an SSO profile)",
        )
    except (ClientError, BotoCoreError) as exc:
        report(
            "AWS credentials",
            False,
            f"STS call failed: {exc}",
            "check AWS_REGION in .env and that your AWS credentials/session are valid",
        )
    except Exception as exc:
        report(
            "AWS credentials",
            False,
            f"unexpected error: {exc}",
            "check your AWS credentials and AWS_REGION in .env",
        )


def check_bedrock_models() -> None:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        report(
            "Bedrock models",
            False,
            "boto3 is not installed",
            "run: pip install -r requirements.txt",
        )
        return

    region = os.environ.get("AWS_REGION")
    if not region:
        report(
            "Bedrock models",
            False,
            "AWS_REGION is not set",
            "copy .env.example to .env and fill in AWS_REGION",
        )
        return

    keywords = ("titan-embed", "claude", "nova")

    try:
        bedrock = boto3.client("bedrock", region_name=region)
        response = bedrock.list_foundation_models()
        summaries = response.get("modelSummaries", [])
        matches = [
            m["modelId"]
            for m in summaries
            if any(k in m["modelId"].lower() for k in keywords)
        ]

        if matches:
            report("Bedrock models", True, f"{len(matches)} matching model(s) available")
            for model_id in sorted(matches):
                print(f"   - {model_id}")
        else:
            report(
                "Bedrock models",
                False,
                "no titan-embed / claude / nova models found in this region",
                "request model access in the Bedrock console (Model access page) for this AWS_REGION",
            )
    except NoCredentialsError:
        report(
            "Bedrock models",
            False,
            "no credentials found",
            "run: aws configure  (or aws sso login if using an SSO profile)",
        )
    except (ClientError, BotoCoreError) as exc:
        report(
            "Bedrock models",
            False,
            f"Bedrock call failed: {exc}",
            "confirm Bedrock is available in AWS_REGION and your IAM role has bedrock:ListFoundationModels",
        )
    except Exception as exc:
        report(
            "Bedrock models",
            False,
            f"unexpected error: {exc}",
            "check AWS_REGION in .env and your AWS credentials",
        )


def main() -> int:
    check_database()
    check_aws_identity()
    check_bedrock_models()

    print()
    if all(results):
        print(f"{CHECK} all checks passed")
        return 0
    print(f"{CROSS} {results.count(False)} check(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
