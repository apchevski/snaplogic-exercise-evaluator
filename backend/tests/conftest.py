"""Shared fixtures: moto-backed AWS + temp evaluator dirs. $0 — no real AWS,
no real Anthropic, no real SnapLogic.

The env vars below MUST be set before anything imports `evaluator.config`
(module-level path constants) or creates a boto3 client, which is why they
live at conftest import time rather than inside fixtures.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# --- environment, before any project import ---
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="evaluator-tests-"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ["TABLE_NAME"] = "evaluator-test-table"
os.environ["DATA_BUCKET"] = "evaluator-test-bucket"
os.environ["ALLOWED_CIDRS"] = ""
os.environ["EVALUATOR_EXERCISES_DIR"] = str(_SESSION_DIR / "exercises")
os.environ["EVALUATOR_TMP_DIR"] = str(_SESSION_DIR / "scratch")
os.environ["EVALUATOR_GRADES_DIR"] = str(_SESSION_DIR / "grades")
os.environ["EVALUATOR_DISABLE_UI_REBUILD"] = "1"

# Repo root on sys.path so `evaluator` and `backend` import in CI.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from backend.src import common  # noqa: E402


@pytest.fixture()
def aws(monkeypatch):
    """Moto context with the single table, data bucket, and job queue."""
    with mock_aws():
        common.reset_cached_clients()

        dynamodb = boto3.client("dynamodb")
        dynamodb.create_table(
            TableName=os.environ["TABLE_NAME"],
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "entity", "AttributeType": "S"},
                {"AttributeName": "slug", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi1",
                    "KeySchema": [
                        {"AttributeName": "entity", "KeyType": "HASH"},
                        {"AttributeName": "slug", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )

        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=os.environ["DATA_BUCKET"])

        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="evaluator-test-jobs")["QueueUrl"]
        monkeypatch.setenv("QUEUE_URL", queue_url)

        yield {"queue_url": queue_url, "s3": s3, "sqs": sqs}

        common.reset_cached_clients()


@pytest.fixture()
def evaluator_dirs():
    """Fresh-but-shared evaluator dirs; tests use unique slugs/students."""
    dirs = {
        "exercises": Path(os.environ["EVALUATOR_EXERCISES_DIR"]),
        "tmp": Path(os.environ["EVALUATOR_TMP_DIR"]),
        "grades": Path(os.environ["EVALUATOR_GRADES_DIR"]),
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def api_event(
    method: str,
    path: str,
    *,
    groups: tuple[str, ...] | None = ("mentor",),
    email: str = "mentor@example.com",
    body: dict | None = None,
    source_ip: str = "10.1.2.3",
) -> dict:
    """Synthetic API Gateway HTTP API (payload v2) event with JWT claims."""
    authorizer: dict = {}
    if groups is not None:
        authorizer = {
            "jwt": {
                "claims": {
                    "email": email,
                    # API Gateway stringifies list claims like this.
                    "cognito:groups": "[" + " ".join(groups) + "]",
                },
                "scopes": [],
            }
        }
    event = {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "testapi",
            "domainName": "testapi.execute-api.us-east-1.amazonaws.com",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": source_ip,
                "userAgent": "pytest",
            },
            "authorizer": authorizer,
            "requestId": "test-request",
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event
