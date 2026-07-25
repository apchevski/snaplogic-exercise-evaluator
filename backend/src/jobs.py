"""JOB-row lifecycle shared by every queued action (grade / sync).

POSTs never do the work inline -- they write a JOB item + an SQS message and
return 202; the worker Lambda owns execution. A conditional-put LOCK item
dedupes concurrent requests for the same target (409 on conflict).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from aws_lambda_powertools.event_handler.exceptions import (
    NotFoundError,
    ServiceError,
)
from boto3.dynamodb.conditions import Key

from .common import (
    JOB_TTL_SECONDS,
    LOCK_TTL_SECONDS,
    dynamo_table,
    epoch_in,
    lock_key,
    public_item,
    query_all,
    sqs_client,
    to_dynamo,
    utc_now_iso,
)

def _acquire_lock(key: str, owner_job_id: str) -> None:
    from botocore.exceptions import ClientError

    try:
        dynamo_table().put_item(
            Item={
                "pk": key,
                "sk": "META",
                "job_id": owner_job_id,
                "created_at": utc_now_iso(),
                "ttl": epoch_in(LOCK_TTL_SECONDS),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ServiceError(
                409,
                "A job for this target is already queued or running. "
                "Wait for it to finish (locks expire after 30 minutes).",
            )
        raise


def _create_job(job_type: str, target: str, payload: dict[str, Any], requested_by: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    _acquire_lock(lock_key(job_type, target), job_id)
    now = utc_now_iso()
    # Payload first so the fixed keys (especially the GSI's `slug`) always win;
    # None values are dropped — a NULL on a GSI key attribute is rejected.
    job = {
        **to_dynamo({k: v for k, v in payload.items() if v is not None}),
        "pk": f"JOB#{job_id}",
        "sk": "META",
        "entity": "job",
        "slug": job_id,
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "target": target,
        "requested_by": requested_by,
        "created_at": now,
        "updated_at": now,
        # Jobs are operational records, not history — REPORT/AUDIT rows keep
        # the durable trail, so let the table's TTL sweep old jobs away.
        "ttl": epoch_in(JOB_TTL_SECONDS),
    }
    dynamo_table().put_item(Item=job)
    sqs_client().send_message(
        QueueUrl=os.environ["QUEUE_URL"],
        MessageBody=json.dumps(
            {"job_id": job_id, "job_type": job_type, "target": target,
             "requested_by": requested_by, **payload}
        ),
    )
    return {"id": job_id, "job_type": job_type, "status": "queued", "target": target}


def _get_job(job_id: str) -> dict[str, Any]:
    resp = dynamo_table().get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"No job {job_id}.")
    return public_item(item)




def _delete_jobs_for_target(job_type: str, target: str) -> int:
    """Drop the JOB rows a deleted entity leaves behind (its job history)."""
    from boto3.dynamodb.conditions import Attr

    table = dynamo_table()
    items = query_all(
        IndexName="gsi1",
        KeyConditionExpression=Key("entity").eq("job"),
        FilterExpression=Attr("target").eq(target) & Attr("job_type").eq(job_type),
    )
    for item in items:
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return len(items)



def _reject_active_job(job_type: str, target: str) -> None:
    """409 while a job for the target is queued or running.

    The job system's own LOCK row is the source of truth; deleting under a
    live job would race the worker, which rewrites cards/reports/artifacts
    when it finishes. An expired-but-unswept lock (DynamoDB TTL cleanup is
    lazy) does not block.
    """
    item = (
        dynamo_table()
        .get_item(Key={"pk": lock_key(job_type, target), "sk": "META"})
        .get("Item")
    )
    if item and int(item.get("ttl", 0)) > epoch_in(0):
        raise ServiceError(
            409,
            f"A {job_type} job for this target is queued or running — "
            "wait for it to finish before deleting.",
        )


