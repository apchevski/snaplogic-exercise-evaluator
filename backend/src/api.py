"""API Lambda: Powertools HTTP router behind the API Gateway JWT authorizer.

Defense layers (outer → inner):
1. CloudFront Function + WAF-free IP allowlist on the SPA (infra).
2. API Gateway JWT authorizer — no valid Cognito token, no Lambda invoke.
3. This handler re-checks the source IP against ALLOWED_CIDRS and enforces
   the role matrix per route (the UI hiding buttons is cosmetic only):

       | Action                          | admin | mentor |
       |---------------------------------|-------|--------|
       | GET  (students/reports/jobs/…)  |  ✅   |  ✅    |
       | POST /v1/gradings               |  ✅   |  ✅    |
       | POST /v1/preps                  |  ✅   |  ❌ 403|

POSTs never do the work inline — they write a JOB item + an SQS message and
return 202; the worker Lambda owns execution. A conditional-put LOCK item
dedupes concurrent requests for the same target (409 on conflict).
"""
from __future__ import annotations

import ipaddress
import json
import os
import uuid
from pathlib import Path
from typing import Any

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
)
from boto3.dynamodb.conditions import Key

from .common import (
    LOCK_TTL_SECONDS,
    data_bucket,
    dynamo_table,
    epoch_in,
    lock_key,
    public_item,
    s3_client,
    slugify,
    sqs_client,
    to_dynamo,
    utc_now_iso,
)

ROLE_ADMIN = "admin"
ROLE_MENTOR = "mentor"

app = APIGatewayHttpResolver()


# ---------- auth helpers ----------


def _claims() -> dict[str, Any]:
    try:
        claims = app.current_event.request_context.authorizer.jwt_claim
    except Exception:
        claims = None
    if not claims:
        raise UnauthorizedError("No JWT claims on the request.")
    return claims


def _groups(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("cognito:groups") or []
    if isinstance(raw, str):
        # API Gateway stringifies list claims as "[admin mentor]".
        raw = raw.strip("[]").replace(",", " ").split()
    return {str(g).strip() for g in raw if str(g).strip()}


def _email(claims: dict[str, Any]) -> str:
    return str(
        claims.get("email")
        or claims.get("username")
        or claims.get("cognito:username")
        or "unknown"
    )


def _require_role(*allowed: str) -> dict[str, Any]:
    claims = _claims()
    groups = _groups(claims)
    if not groups.intersection(allowed):
        raise ServiceError(
            403, f"Requires one of roles {sorted(allowed)}; token has {sorted(groups)}."
        )
    return claims


def _ip_allowed(source_ip: str) -> bool:
    cidrs = [c.strip() for c in os.environ.get("ALLOWED_CIDRS", "").split(",") if c.strip()]
    if not cidrs:
        return True  # allowlist disabled; CloudFront/API GW layer still applies
    try:
        ip = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(c, strict=False) for c in cidrs)


# ---------- job creation ----------


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


# ---------- read routes (any authenticated user) ----------


@app.get("/v1/students")
def list_students() -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    students = sorted(
        (public_item(i) for i in resp.get("Items", [])),
        key=lambda s: str(s.get("display_name", "")).lower(),
    )
    return {"students": students}


@app.get("/v1/students/<slug>")
def get_student(slug: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    resp = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"No graded student {slug!r}.")
    meta = public_item(item)
    report = None
    # "report_json" is the legacy attribute name written before the store
    # labels were fixed; keep reading it so old gradings stay viewable.
    key = meta.get("report_json_key") or meta.get("report_json")
    if key:
        obj = s3_client().get_object(Bucket=data_bucket(), Key=key)
        report = json.loads(obj["Body"].read().decode("utf-8"))
    return {"student": meta, "report": report}


@app.get("/v1/students/<slug>/reports")
def list_student_reports(slug: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    resp = dynamo_table().query(
        KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}")
        & Key("sk").begins_with("REPORT#"),
        ScanIndexForward=False,
    )
    return {"reports": [public_item(i) for i in resp.get("Items", [])]}


@app.get("/v1/exercises")
def list_exercises() -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    # Authored folders ship in this image; prep state lives in DynamoDB.
    from evaluator.tasks import (
        list_exercise_folders,
        list_exercise_resources,
        read_exercise_description,
        read_pipeline_name_from_description,
    )

    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("exercise")
    )
    by_slug = {str(i.get("slug")): public_item(i) for i in resp.get("Items", [])}
    exercises = []
    for folder in list_exercise_folders():
        entry = by_slug.pop(folder, None) or {
            "slug": folder,
            "prep_status": "never_prepped",
        }
        entry.setdefault("title", read_pipeline_name_from_description(folder) or folder)
        entry.setdefault("max_points", 10)
        entry["description"] = read_exercise_description(folder)
        entry["resources"] = list_exercise_resources(folder)
        exercises.append(entry)
    # Exercises known to DynamoDB but missing from the image (e.g. folder
    # deleted in git) still show up, flagged.
    for slug, entry in sorted(by_slug.items()):
        entry["missing_from_image"] = True
        exercises.append(entry)
    return {"exercises": exercises}


RESOURCE_URL_TTL_SECONDS = 300


def _sync_resource_to_s3(path: Path, key: str) -> None:
    """Mirror one baked-in resource file into S3 (image copy is canonical).

    Skips the upload when S3 already holds byte-identical content (single
    part uploads: ETag == content MD5), so repeat downloads cost one
    HeadObject. Files live under ``exercise-resources/`` — deliberately
    outside the worker-owned ``exercises/`` prefix, which S3Store
    re-downloads wholesale on every job.
    """
    import hashlib

    from botocore.exceptions import ClientError

    body = path.read_bytes()
    md5 = hashlib.md5(body).hexdigest()
    s3 = s3_client()
    try:
        head = s3.head_object(Bucket=data_bucket(), Key=key)
        if head.get("ETag", "").strip('"') == md5:
            return
    except ClientError as e:
        # "403": S3 masks HeadObject-on-missing-key as Forbidden when the
        # caller lacks s3:ListBucket. Upload anyway — worst case we re-put
        # identical bytes; raising here turns IAM drift into a 500.
        if e.response["Error"]["Code"] not in ("403", "404", "NoSuchKey", "NotFound"):
            raise
    s3.put_object(Bucket=data_bucket(), Key=key, Body=body)


@app.get("/v1/exercises/<slug>/resources/<filename>")
def get_exercise_resource(slug: str, filename: str) -> dict[str, Any]:
    """Short-lived presigned download URL for one student input file.

    Streaming ~4 MB zips through Lambda would flirt with the 6 MB response
    ceiling once base64-encoded, so the browser downloads straight from S3
    instead: lazily mirror the image's copy there, then presign a GET.
    """
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    from evaluator.tasks import exercise_resource_path

    path = exercise_resource_path(slug, filename)
    if path is None:
        raise NotFoundError(f"No resource file {filename!r} for exercise {slug!r}.")
    key = f"exercise-resources/{slug}/{path.name}"
    _sync_resource_to_s3(path, key)
    url = s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": data_bucket(),
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{path.name}"',
        },
        ExpiresIn=RESOURCE_URL_TTL_SECONDS,
    )
    return {"filename": path.name, "url": url, "expires_in": RESOURCE_URL_TTL_SECONDS}


@app.get("/v1/gradings/<job_id>")
def get_grading(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)


@app.get("/v1/preps/<job_id>")
def get_prep(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)


# ---------- write routes ----------


@app.post("/v1/gradings")
def post_grading() -> Response:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    student = str(body.get("student") or "").strip()
    if not student:
        raise BadRequestError("Body must include a non-empty 'student'.")
    payload = {
        "student": student,
        "student_slug": slugify(student),
        "space": (str(body.get("space")).strip() or None) if body.get("space") else None,
        "task": (str(body.get("task")).strip() or None) if body.get("task") else None,
    }
    job = _create_job("grade", slugify(student), payload, _email(claims))
    return Response(
        status_code=202, content_type="application/json", body=json.dumps(job)
    )


@app.post("/v1/preps")
def post_prep() -> Response:
    claims = _require_role(ROLE_ADMIN)  # mentors get 403 here
    body = app.current_event.json_body or {}
    slug = str(body.get("slug") or "").strip()
    target = slug or "all"
    if slug:
        from evaluator.tasks import list_exercise_folders

        if slug not in list_exercise_folders():
            raise BadRequestError(
                f"Unknown exercise folder {slug!r}. Omit 'slug' to prep everything."
            )
    job = _create_job("prep", target, {"exercise_slug": slug or None}, _email(claims))
    return Response(
        status_code=202, content_type="application/json", body=json.dumps(job)
    )


# ---------- entry point ----------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    source_ip = (
        (event.get("requestContext") or {}).get("http", {}).get("sourceIp", "")
    )
    if not _ip_allowed(source_ip):
        return {
            "statusCode": 403,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": "Source IP not in the allowlist."}),
        }
    return app.resolve(event, context)
