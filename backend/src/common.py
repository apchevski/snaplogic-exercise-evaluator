"""Shared helpers for the API and worker Lambdas.

Single DynamoDB table (see infra/modules/data). Item shapes:

    STUDENT#<slug>  / META            entity="student", slug — student card
    STUDENT#<slug>  / REPORT#<ver>    one row per grading run (history)
    JOB#<id>        / META            entity="job", slug=<id> — job lifecycle
    LOCK#<key>      / META            conditional-put dedupe lock, ttl 30 min
    EXERCISE#<slug> / META            entity="exercise", slug — prep status

GSI ``gsi1`` is sparse on (entity, slug): only items that carry both
attributes are listable, which keeps REPORT/LOCK rows out of list queries.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from decimal import Decimal
from functools import lru_cache
from typing import Any

LOCK_TTL_SECONDS = 30 * 60


def table_name() -> str:
    return os.environ["TABLE_NAME"]


def data_bucket() -> str:
    return os.environ["DATA_BUCKET"]


@lru_cache(maxsize=1)
def dynamo_table():
    import boto3

    return boto3.resource("dynamodb").Table(table_name())


@lru_cache(maxsize=1)
def s3_client():
    import boto3

    return boto3.client("s3")


@lru_cache(maxsize=1)
def sqs_client():
    import boto3

    return boto3.client("sqs")


def reset_cached_clients() -> None:
    """Test hook: drop cached boto3 resources so moto fixtures apply."""
    dynamo_table.cache_clear()
    s3_client.cache_clear()
    sqs_client.cache_clear()


def utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def epoch_in(seconds: int) -> int:
    return int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp()) + seconds


def slugify(name: str) -> str:
    """Stable URL/key-safe slug for student display names and similar."""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm or "unnamed"


def lock_key(job_type: str, target: str) -> str:
    return f"LOCK#{job_type}#{target}"


def to_dynamo(obj: Any) -> Any:
    """Make a JSON-shaped object storable (floats → Decimal)."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_dynamo(obj: Any) -> Any:
    """Make a DynamoDB item JSON-serializable (Decimal → int/float)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [from_dynamo(v) for v in obj]
    return obj


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Strip table internals before returning an item over the API."""
    cleaned = {k: v for k, v in item.items() if k not in {"pk", "sk", "ttl"}}
    return from_dynamo(cleaned)
