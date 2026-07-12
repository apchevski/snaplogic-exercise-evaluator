"""API Lambda: Powertools HTTP router behind the API Gateway JWT authorizer.

Defense layers (outer → inner):
1. CloudFront Function + WAF-free IP allowlist on the SPA (infra).
2. API Gateway JWT authorizer — no valid Cognito token, no Lambda invoke.
3. This handler re-checks the source IP against ALLOWED_CIDRS and enforces
   the role matrix per route (the UI hiding buttons is cosmetic only):

       | Action                            | admin | mentor | student |
       |-----------------------------------|-------|--------|---------|
       | GET  /v1/exercises / resources    |  ✅   |  ✅    |  ✅ |
       | GET  /v1/students (list)          |  ✅   |  ✅    |  ✅ all rows; others slimmed |
       | GET  /v1/students/{slug} (+ /reports) | ✅ | ✅    |  ✅ own card only (else 403) |
       | GET  /v1/config, /v1/exercises/{slug} (authored content incl. notes.md), job polling | ✅ | ✅ | ❌ 403 |
       | GET/PUT /v1/settings (own credentials + judge model) | ✅ | ✅ (no SnapLogic creds) | ❌ 403 |
       | GET  /v1/students/{slug}/report/edits (audit log) | ✅ | ✅ | ❌ 403 |
       | POST /v1/students                 |  ✅   |  ✅    |  ❌ 403 |
       | POST /v1/gradings                 |  ✅   |  ✅    |  ❌ 403 |
       | PATCH /v1/students/{slug}/report  |  ✅   |  ✅    |  ❌ 403 |
       | POST /v1/syncs                    |  ✅   |  ❌ 403|  ❌ 403 |
       | POST /v1/exercises                |  ✅   |  ❌ 403|  ❌ 403 |
       | PUT  /v1/exercises/{slug}         |  ✅   |  ❌ 403|  ❌ 403 |
       | DELETE /v1/students/{slug}        |  ✅   |  ❌ 403|  ❌ 403 |
       | DELETE /v1/exercises/{slug}       |  ✅   |  ❌ 403|  ❌ 403 |

   `student` is the read-only role: members are created by POST /v1/students
   when a registration carries an email (Cognito emails the temporary
   password; the hosted UI forces a change on first sign-in). A student sees
   the whole roster — every row of the dashboard table — but rows that are
   not their own are slimmed to the table's columns (STUDENT_ROSTER_KEYS: no
   email, no summary, no report or provenance fields), and the per-student
   detail reads 403 on anyone else's slug — another student's detailed
   evaluation stays private. They can also browse the exercise list
   (descriptions + input files) but start or change nothing, and never see
   instructor notes. The link between a Cognito login and its card is the
   email: it is stored (lowercased) on the STUDENT card created alongside
   the login, and matched against the caller's email claim here.

POSTs never do the work inline — they write a JOB item + an SQS message and
return 202; the worker Lambda owns execution. A conditional-put LOCK item
dedupes concurrent requests for the same target (409 on conflict).
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
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
    apply_user_overrides,
    cognito_client,
    data_bucket,
    dynamo_table,
    epoch_in,
    from_dynamo,
    get_user_settings,
    load_secrets_into_env,
    lock_key,
    public_item,
    s3_client,
    shared_judge_model,
    slugify,
    sqs_client,
    to_dynamo,
    user_settings_pk,
    utc_now_iso,
)

ROLE_ADMIN = "admin"
ROLE_MENTOR = "mentor"
ROLE_STUDENT = "student"

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


def _is_student_only(claims: dict[str, Any]) -> bool:
    """True for a caller in the read-only `student` group and nothing more
    privileged. Such users are scoped to their own STUDENT card; an
    admin/mentor (even one who is also in `student`) sees everything."""
    groups = _groups(claims)
    return ROLE_STUDENT in groups and not groups.intersection({ROLE_ADMIN, ROLE_MENTOR})


def _own_student_slug(claims: dict[str, Any]) -> str | None:
    """The STUDENT card slug whose login email matches the caller, or None.

    A student login is created together with its card (POST /v1/students with
    an email), which stores that email lowercased on the card — so the email
    claim is the link between the Cognito identity and the dashboard row.
    """
    email = _email(claims).strip().lower()
    if not email or email == "unknown":
        return None
    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    for item in resp.get("Items", []):
        if str(item.get("email") or "").strip().lower() == email:
            return str(item.get("slug"))
    return None


def _require_own_card(claims: dict[str, Any], slug: str) -> None:
    """Confine a student to their own card. Admins and mentors may read any
    student's card; a student reading anyone else's slug gets a 403."""
    if _is_student_only(claims) and _own_student_slug(claims) != slug:
        raise ServiceError(403, "Students may only view their own grades.")


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


# ---------- S3-authored exercises ----------
#
# S3 (under exercises/<slug>/) is the canonical home of authored exercise
# content — description.md, notes.md, resources/* — created and edited from
# the UI. The worker overlays the whole prefix onto the image tree before
# every job (S3Store.materialize_exercises), so sync and grade see authored
# exercises exactly as if the folders were committed. Folders that still ship
# in the image (git fallback / pre-migration) are seeded into S3 by the next
# sync job. Only the API writes description.md into the prefix (sync uploads
# task.json / solution.json / expected/ only), so its presence in S3 marks an
# authored slug. Type-specific config (the old hand-written task.json) is
# structured data on the EXERCISE row (`task_config`); the worker synthesizes
# task.json from it at sync time.

AUTHORED_PREFIX = "exercises/"
UPLOAD_URL_TTL_SECONDS = 900
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SCENARIO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


def _clean_filename(raw: Any, *, label: str, seen: list[str]) -> str:
    """One plain, unique filename from user input — or BadRequestError."""
    name = str(raw or "").strip()
    if not name or name != Path(name).name or name in seen:
        raise BadRequestError(f"Invalid or duplicate {label} {name!r}.")
    seen.append(name)
    return name


def _validate_task_config(raw: Any) -> dict[str, Any] | None:
    """Normalize the structured task config from the create/edit dialog.

    None means "auto": a single-output file_writer exercise needs no config —
    sync derives task.json from the solution pipeline's lone writer snap.
    The returned dict is stored on the EXERCISE row; the worker synthesizes
    task.json from it (plus the env-derived pipeline path) at sync time.
    """
    if raw is None or raw == {} or raw == "":
        return None
    if not isinstance(raw, dict):
        raise BadRequestError("task_config must be an object (or null for auto).")
    task_type = str(raw.get("task_type") or "").strip()

    if task_type == "file_writer":
        filenames = raw.get("output_filenames")
        if not isinstance(filenames, list) or not filenames:
            raise BadRequestError(
                "file_writer task_config needs a non-empty 'output_filenames' array."
            )
        names: list[str] = []
        for f in filenames:
            _clean_filename(f, label="output filename", seen=names)
        mode = str(raw.get("output_match_mode") or "exact").strip()
        if mode not in ("exact", "columns_only"):
            raise BadRequestError(
                "output_match_mode must be 'exact' or 'columns_only'."
            )
        return {
            "task_type": "file_writer",
            "output_filenames": names,
            "output_match_mode": mode,
        }

    if task_type == "triggered_task":
        task_name = str(raw.get("triggered_task_name") or "").strip()
        if not task_name:
            raise BadRequestError(
                "triggered_task task_config needs a non-empty 'triggered_task_name'."
            )
        scenarios = raw.get("requests")
        if not isinstance(scenarios, list) or not scenarios:
            raise BadRequestError(
                "triggered_task task_config needs a non-empty 'requests' array."
            )
        parsed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for s in scenarios:
            if not isinstance(s, dict):
                raise BadRequestError("Each request must be an object with 'name' + 'params'.")
            name = str(s.get("name") or "").strip()
            if not _SCENARIO_NAME_RE.match(name) or name in seen:
                raise BadRequestError(
                    f"Invalid or duplicate scenario name {name!r} — lowercase "
                    f"letters, digits and '_' only (it becomes a filename in expected/)."
                )
            seen.add(name)
            params = s.get("params") or {}
            if not isinstance(params, dict):
                raise BadRequestError(f"Scenario {name!r}: 'params' must be an object.")
            parsed.append(
                {"name": name, "params": {str(k): str(v) for k, v in params.items()}}
            )
        return {
            "task_type": "triggered_task",
            "triggered_task_name": task_name,
            "requests": parsed,
        }

    raise BadRequestError(
        "task_config.task_type must be 'file_writer' or 'triggered_task' "
        "(omit task_config entirely for a single-output file-writer exercise)."
    )


def _h1_title(markdown: str) -> str | None:
    """First H1 heading — same rule as tasks.read_pipeline_name_from_description."""
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip() or None
    return None


def _scan_authored_s3() -> dict[str, list[dict[str, Any]]]:
    """One paginated LIST over exercises/ → {slug: [resource entries]}.

    A slug counts as S3-authored only when S3 holds its description.md;
    sync-generated artifacts sharing the prefix never include one.
    """
    slugs: dict[str, dict[str, Any]] = {}
    paginator = s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=data_bucket(), Prefix=AUTHORED_PREFIX):
        for obj in page.get("Contents", []):
            parts = obj["Key"][len(AUTHORED_PREFIX):].split("/")
            entry = slugs.setdefault(parts[0], {"authored": False, "resources": []})
            if parts[1:] == ["description.md"]:
                entry["authored"] = True
            elif len(parts) == 3 and parts[1] == "resources" and parts[2]:
                entry["resources"].append(
                    {"filename": parts[2], "size_bytes": int(obj["Size"])}
                )
    return {
        slug: sorted(e["resources"], key=lambda r: str(r["filename"]).lower())
        for slug, e in slugs.items()
        if e["authored"]
    }


def _s3_text(key: str) -> str | None:
    from botocore.exceptions import ClientError

    try:
        obj = s3_client().get_object(Bucket=data_bucket(), Key=key)
    except ClientError:
        return None
    return obj["Body"].read().decode("utf-8")


def _known_exercise_slugs() -> set[str]:
    from evaluator.tasks import list_exercise_folders

    return set(list_exercise_folders()) | set(_scan_authored_s3())


def _normalize_sync_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Back-compat: emit the post-rename attribute names for old rows.

    EXERCISE rows written before the prep→sync rename carry `prep_status` /
    `last_prepped_at` / the `never_prepped` value. Surface them under the new
    names (`sync_status` / `last_synced_at` / `never_synced`) so the API is
    uniform; old rows migrate to the new attributes on their next sync-job
    put_item (or the next edit).
    """
    if "prep_status" in item:
        item.setdefault("sync_status", item.pop("prep_status"))
    if item.get("sync_status") == "never_prepped":
        item["sync_status"] = "never_synced"
    if "last_prepped_at" in item:
        item.setdefault("last_synced_at", item.pop("last_prepped_at"))
    return item


def _exercise_row(slug: str) -> dict[str, Any] | None:
    item = (
        dynamo_table().get_item(Key={"pk": f"EXERCISE#{slug}", "sk": "META"}).get("Item")
    )
    return _normalize_sync_fields(from_dynamo(item)) if item else None


def _reject_archived(slug: str, action: str) -> None:
    row = _exercise_row(slug)
    if row and row.get("deleted"):
        # Tombstone of a hard-deleted exercise whose folder still ships in
        # the image (see delete_exercise) — the slug looks "known" but is gone.
        raise BadRequestError(f"Exercise {slug!r} was deleted.")
    if row and row.get("archived"):
        raise BadRequestError(
            f"Exercise {slug!r} is archived; unarchive it before you {action} it."
        )


# ---------- read routes (any authenticated user) ----------


@app.get("/v1/config")
def get_config() -> dict[str, Any]:
    """Non-secret SnapLogic settings the UI needs (e.g. to prefill the
    Add Student dialog's project space). Credentials never leave the server."""
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    load_secrets_into_env()

    def env(key: str) -> str | None:
        return os.environ.get(key, "").strip() or None

    return {
        "config": {
            "org_name": env("SNAPLOGIC_ORG_NAME"),
            "student_project_space": env("SNAPLOGIC_STUDENT_PROJECT_SPACE"),
            "solution_project_space": env("SNAPLOGIC_SOLUTION_PROJECT_SPACE"),
            "solution_project": env("SNAPLOGIC_SOLUTION_PROJECT"),
        }
    }


# ---------- per-user settings (own credentials + judge model) ----------

#: Judge models a user may pick in Settings. Kept in lockstep with the
#: pricing table in evaluator.ai_judge so cost estimates stay accurate —
#: the description cost ratios come from _PRICING_PER_MTOK (Sonnet $3/$15,
#: Opus $5/$25, Haiku $1/$5 per MTok).
ALLOWED_JUDGE_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5 (Recommended)",
        "description": "Best balance of grading quality and cost",
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "description": "Previous Sonnet · same price, the default before Sonnet 5",
    },
    {
        "id": "claude-opus-4-8",
        "label": "Claude Opus 4.8",
        "description": "Most thorough evaluations · ~1.7× the cost of Sonnet",
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "description": "Fastest, ~⅓ the cost of Sonnet · lighter judgment",
    },
)

#: SETTINGS-row keys only an admin may write (mentors run grading against the
#: shared SnapLogic credentials; syncs are admin-only anyway).
_ADMIN_ONLY_SETTINGS_KEYS = ("snaplogic_username", "snaplogic_password")


def _masked_settings(email: str, row: dict[str, Any]) -> dict[str, Any]:
    """The caller-visible view of their SETTINGS row — secrets never leave
    the server; the UI only learns *that* a value is stored (plus a short
    tail of the API key so the owner can tell which key it is)."""
    api_key = str(row.get("anthropic_api_key") or "")
    return {
        "email": email,
        "snaplogic_username": str(row.get("snaplogic_username") or "") or None,
        "snaplogic_password_set": bool(str(row.get("snaplogic_password") or "").strip()),
        "anthropic_api_key_set": bool(api_key.strip()),
        "anthropic_api_key_hint": ("…" + api_key[-4:]) if len(api_key) >= 12 else None,
        "judge_model": str(row.get("judge_model") or "") or None,
        "default_model": shared_judge_model(),
        "allowed_models": [dict(m) for m in ALLOWED_JUDGE_MODELS],
        "updated_at": row.get("updated_at"),
    }


@app.get("/v1/settings")
def get_settings() -> dict[str, Any]:
    """The caller's own stored credentials (masked) + the model choices."""
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    email = _email(claims).strip().lower()
    return {"settings": _masked_settings(email, get_user_settings(email))}


@app.put("/v1/settings")
def put_settings() -> dict[str, Any]:
    """Partial update of the caller's own credentials and judge model.

    Accepted keys — only the ones present are applied; null or "" clears:
      snaplogic_username    admin only — personal SnapLogic login
      snaplogic_password    admin only — stored write-only, never returned
      anthropic_api_key     own Anthropic key for grading (admin or mentor)
      judge_model           model used when this user starts a grading; must
                            be one of ALLOWED_JUDGE_MODELS (null = default)

    Jobs started by this user (grade, sync, the registration project check)
    run under these values; anything unset falls back to the shared app
    secret. SnapLogic credentials only take effect as a complete
    username+password pair.
    """
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    if not isinstance(body, dict):
        raise BadRequestError("Body must be a JSON object.")
    email = _email(claims).strip().lower()
    if not email or email == "unknown":
        raise BadRequestError(
            "Your token carries no email claim — settings need one to be stored."
        )
    is_admin = ROLE_ADMIN in _groups(claims)

    editable = (
        "snaplogic_username",
        "snaplogic_password",
        "anthropic_api_key",
        "judge_model",
    )
    unknown = sorted(set(body) - set(editable))
    if unknown:
        raise BadRequestError(f"Unknown settings key(s): {', '.join(unknown)}.")
    for key in _ADMIN_ONLY_SETTINGS_KEYS:
        if key in body and not is_admin:
            raise ServiceError(
                403, "Only admins may store personal SnapLogic credentials."
            )

    if "judge_model" in body and body.get("judge_model"):
        model = str(body["judge_model"]).strip()
        allowed = {m["id"] for m in ALLOWED_JUDGE_MODELS}
        if model not in allowed:
            raise BadRequestError(
                f"judge_model must be one of {sorted(allowed)} (or null for the default)."
            )

    row = get_user_settings(email)
    for key in editable:
        if key not in body:
            continue
        value = str(body.get(key) or "").strip()
        if value:
            row[key] = value
        else:
            row.pop(key, None)

    row.update(
        {
            "pk": user_settings_pk(email),
            "sk": "SETTINGS",
            "email": email,
            "updated_at": utc_now_iso(),
        }
    )
    dynamo_table().put_item(Item=to_dynamo(row))
    return {"settings": _masked_settings(email, row)}


# The roster columns the dashboard table shows — the only fields another
# student's row carries when a student-only caller lists the roster.
# Everything else (email, overall summary, report keys, registration and
# edit provenance) stays between that student and the staff.
STUDENT_ROSTER_KEYS = {
    "slug",
    "display_name",
    "space",
    "project",
    "points_earned",
    "points_possible",
    "counts",
    "graded_at",
}


@app.get("/v1/students")
def list_students() -> dict[str, Any]:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    students = sorted(
        (public_item(i) for i in resp.get("Items", [])),
        key=lambda s: str(s.get("display_name", "")).lower(),
    )
    if _is_student_only(claims):
        # A student sees the whole roster (scores are cohort-visible), but
        # every row that isn't their own is slimmed to the table's columns.
        # Their own card (matched by email) stays complete — its email is how
        # the SPA finds "my" row, and the per-slug detail endpoints still 403
        # on anyone else's slug.
        email = _email(claims).strip().lower()
        students = [
            s
            if str(s.get("email") or "").strip().lower() == email
            else {k: v for k, v in s.items() if k in STUDENT_ROSTER_KEYS}
            for s in students
        ]
    return {"students": students}


@app.get("/v1/students/<slug>")
def get_student(slug: str) -> dict[str, Any]:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    _require_own_card(claims, slug)
    resp = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"No graded student {slug!r}.")
    meta = public_item(item)
    # So the detail view can show the project path even before the first grade.
    meta["student_project_path"] = _student_project_path(meta)
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
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    _require_own_card(claims, slug)
    resp = dynamo_table().query(
        KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}")
        & Key("sk").begins_with("REPORT#"),
        ScanIndexForward=False,
    )
    return {"reports": [public_item(i) for i in resp.get("Items", [])]}


@app.get("/v1/exercises")
def list_exercises() -> dict[str, Any]:
    # Students may look: the listing carries descriptions and input files but
    # never notes.md (instructor hints live behind GET /v1/exercises/{slug}).
    _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    # Authored folders ship in this image; sync state lives in DynamoDB.
    from evaluator.tasks import (
        TASK_TYPE_FILE_WRITER,
        list_exercise_folders,
        list_exercise_resources,
        read_exercise_description,
        read_pipeline_name_from_description,
        read_task_type,
    )

    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("exercise")
    )
    by_slug = {
        str(i.get("slug")): _normalize_sync_fields(public_item(i))
        for i in resp.get("Items", [])
    }
    # Hard-deleted exercises whose folder still ships in the image keep a
    # tombstone row (see delete_exercise); they must not resurface anywhere.
    deleted_slugs = {s for s, e in by_slug.items() if e.get("deleted")}
    by_slug = {s: e for s, e in by_slug.items() if s not in deleted_slugs}
    authored = _scan_authored_s3()
    exercises = []
    for folder in list_exercise_folders():
        if folder in deleted_slugs:
            authored.pop(folder, None)
            continue
        # S3 is the canonical authored store: once an exercise exists there
        # (UI-created, or seeded from the image by a sync job), its S3
        # description wins over the image copy — a UI edit must show even
        # when the image still ships the original. Image files only fill
        # gaps (e.g. resources that predate the S3 seed).
        s3_files = authored.pop(folder, None)
        entry = by_slug.pop(folder, None) or {
            "slug": folder,
            "sync_status": "never_synced",
        }
        entry.setdefault("max_points", 10)
        if s3_files is not None:
            text = _s3_text(f"{AUTHORED_PREFIX}{folder}/description.md") or ""
            entry["description"] = text.strip() or None
            entry.setdefault("title", _h1_title(text) or folder)
            shipped = {r["filename"] for r in s3_files}
            entry["resources"] = s3_files + [
                r for r in list_exercise_resources(folder) if r["filename"] not in shipped
            ]
        else:
            entry.setdefault(
                "title", read_pipeline_name_from_description(folder) or folder
            )
            entry["description"] = read_exercise_description(folder)
            entry["resources"] = list_exercise_resources(folder)
        exercises.append(entry)
    # Exercises authored in S3 with no image folder at all (the normal case
    # for UI-created exercises).
    for slug in sorted(authored):
        if slug in deleted_slugs:
            continue
        entry = by_slug.pop(slug, None) or {
            "slug": slug,
            "sync_status": "never_synced",
        }
        text = _s3_text(f"{AUTHORED_PREFIX}{slug}/description.md") or ""
        entry.setdefault("title", _h1_title(text) or slug)
        entry.setdefault("max_points", 10)
        entry["description"] = text.strip() or None
        entry["resources"] = authored[slug]
        exercises.append(entry)
    # Exercises known to DynamoDB but missing from the image (e.g. folder
    # deleted in git) still show up, flagged.
    for slug, entry in sorted(by_slug.items()):
        entry["missing_from_image"] = True
        exercises.append(entry)
    # The top-level `task_type` is only stamped on the row at sync time, so a
    # freshly authored exercise would otherwise show a dash in the Task Type
    # column. Backfill it for the listing from the author's choice: the
    # structured task_config (file_writer / triggered_task), then the on-disk
    # task.json (image exercises), then file_writer — the type an auto /
    # single-output exercise (no config at all) syncs into.
    for entry in exercises:
        if entry.get("task_type"):
            continue
        cfg = entry.get("task_config")
        if isinstance(cfg, dict) and cfg.get("task_type"):
            entry["task_type"] = str(cfg["task_type"])
        else:
            entry["task_type"] = read_task_type(entry["slug"]) or TASK_TYPE_FILE_WRITER
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
    _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    from evaluator.tasks import exercise_resource_path

    row = _exercise_row(slug)
    if row and row.get("deleted"):
        # Hard-deleted exercise (image copy tombstoned) — without this check
        # the lazy mirror below would resurrect its files into S3.
        raise NotFoundError(f"No exercise {slug!r}.")
    path = exercise_resource_path(slug, filename)
    if path is not None:
        name = path.name
        key = f"exercise-resources/{slug}/{name}"
        _sync_resource_to_s3(path, key)
    else:
        # S3-authored exercise: the canonical copy already lives in S3, so
        # presign it directly — no mirroring step.
        name = filename
        key = _authored_resource_key(slug, filename)
        if key is None:
            raise NotFoundError(f"No resource file {filename!r} for exercise {slug!r}.")
    url = s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": data_bucket(),
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{name}"',
        },
        ExpiresIn=RESOURCE_URL_TTL_SECONDS,
    )
    return {"filename": name, "url": url, "expires_in": RESOURCE_URL_TTL_SECONDS}


def _authored_resource_key(slug: str, filename: str) -> str | None:
    """S3 key of one UI-authored input file, or None (callers 404 on None).

    Same sanitization rule as tasks.exercise_resource_path: reject anything
    that isn't a plain filename, then require the object to exist.
    """
    from botocore.exceptions import ClientError

    if not _SLUG_RE.match(slug):
        return None
    if not filename or filename != Path(filename).name:
        return None
    key = f"{AUTHORED_PREFIX}{slug}/resources/{filename}"
    try:
        s3_client().head_object(Bucket=data_bucket(), Key=key)
    except ClientError:
        return None
    return key


@app.get("/v1/gradings/<job_id>")
def get_grading(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)


@app.get("/v1/syncs/<job_id>")
def get_sync(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)


# ---------- write routes ----------


def _default_student_space() -> str | None:
    """The configured default student project space, if any."""
    load_secrets_into_env()
    return os.environ.get("SNAPLOGIC_STUDENT_PROJECT_SPACE", "").strip() or None


def _student_project_path(meta: dict[str, Any]) -> str | None:
    """The SnapLogic path to the student's project — ``org/space/project`` —
    mirroring the manifest grade.py writes. Lets the UI show the project path
    even for students who've never been graded (no report to read it from).
    Returns None when org/space can't be resolved (e.g. credential-less env)."""
    load_secrets_into_env()
    org = os.environ.get("SNAPLOGIC_ORG_NAME", "").strip()
    space = (meta.get("space") or "").strip() or _default_student_space()
    # The project defaults to the student name (same rule as grade.py).
    project = (meta.get("project") or "").strip() or meta.get("display_name")
    if not (org and space and project):
        return None
    return f"{org}/{space}/{project}"


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    """Trimmed optional string from the request body (empty → None)."""
    raw = body.get(key)
    if raw is None:
        return None
    return str(raw).strip() or None


def _verify_student_project(project: str, space: str | None, requester: str) -> None:
    """Reject registration when SnapLogic has no project with that name.

    The project (by default named exactly after the student) must exist in
    the student project space, or every subsequent grading run would fail.
    One GET (asset list) settles it: 404 → clear 400 back to the UI.
    Credentials are the requester's own stored SnapLogic login when set,
    otherwise the app secret (deployed) or the ambient env (local dev); when
    none are configured at all the check is skipped so registration keeps
    working in credential-less environments (e.g. tests).
    """
    import httpx

    apply_user_overrides(requester)
    base_url = os.environ.get("SNAPLOGIC_BASE_URL", "").strip().rstrip("/")
    username = os.environ.get("SNAPLOGIC_ADMIN_USERNAME", "").strip()
    password = os.environ.get("SNAPLOGIC_ADMIN_PASSWORD", "").strip()
    org = os.environ.get("SNAPLOGIC_ORG_NAME", "").strip()
    if not (base_url and username and password and org):
        return
    ps = space or _default_student_space() or "IWC_Support"

    from evaluator.config import Settings
    from evaluator.snaplogic_client import SnapLogicClient

    settings = Settings(
        base_url=base_url,
        username=username,
        password=password,
        org_name=org,
        project_space_name="",
        project_name="",
        student_project_space_name=ps,
    )
    # Stay well under the 29 s API Gateway ceiling.
    with SnapLogicClient(settings, timeout_s=10.0) as client:
        try:
            client.list_assets(org, ps, project)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BadRequestError(
                    f"No project named {project!r} exists in the {ps!r} "
                    "project space — check the project space and project "
                    "name (the project defaults to the student name)."
                )
            raise ServiceError(
                502,
                "Could not verify the SnapLogic project "
                f"(SnapLogic answered HTTP {e.response.status_code}). Try again.",
            )
        except httpx.HTTPError as e:
            raise ServiceError(
                502, f"Could not reach SnapLogic to verify the project: {e}"
            )


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _create_student_login(student: str, email: str) -> None:
    """Invite the student into Cognito's read-only `student` group.

    AdminCreateUser with EMAIL delivery makes Cognito send the invitation
    itself (username + temporary password); the hosted UI then forces a
    password change on first sign-in — the API never sees or stores a
    password. The email doubles as the username (the pool signs in by
    email), and is pre-verified so account recovery works immediately.
    """
    from botocore.exceptions import ClientError

    pool_id = os.environ.get("USER_POOL_ID", "").strip()
    if not pool_id:
        raise ServiceError(
            503,
            "Student logins are not configured on this deployment "
            "(USER_POOL_ID is unset). Register without an email, or deploy "
            "the Cognito wiring first.",
        )
    cognito = cognito_client()
    try:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": student},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "UsernameExistsException":
            raise ServiceError(409, f"A login for {email!r} already exists.")
        if code == "InvalidParameterException":
            raise BadRequestError(
                f"Cognito rejected the login for {email!r}: "
                f"{e.response['Error'].get('Message', 'invalid parameter')}"
            )
        raise ServiceError(502, f"Creating the student login failed: {e}")
    try:
        cognito.admin_add_user_to_group(
            UserPoolId=pool_id, Username=email, GroupName=ROLE_STUDENT
        )
    except ClientError as e:
        # Without the group the login can't pass any role check — remove it
        # so a retry doesn't hit UsernameExistsException on a broken account.
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)
        raise ServiceError(502, f"Assigning the student role failed: {e}")


def _delete_student_login(email: str) -> bool:
    """Best-effort removal of the login a registration created (see
    delete_student: a hard delete leaves no trace, and an orphaned login
    could still sign in and read every grade)."""
    from botocore.exceptions import ClientError

    pool_id = os.environ.get("USER_POOL_ID", "").strip()
    if not pool_id:
        return False
    try:
        cognito_client().admin_delete_user(UserPoolId=pool_id, Username=email)
    except ClientError as e:
        if e.response["Error"]["Code"] == "UserNotFoundException":
            return False
        raise
    return True


@app.post("/v1/students")
def post_student() -> Response:
    """Register a student without grading anything (admin or mentor).

    Creates the STUDENT card so the student shows up on the dashboard with
    every exercise still ungraded; a full or per-exercise grading can then
    be started later. Optional body keys 'space' (project space; defaults to
    SNAPLOGIC_STUDENT_PROJECT_SPACE) and 'project' (SnapLogic project name;
    defaults to the student name) are stored on the card and dictate where
    every later grading run looks for this student's pipelines. Optional
    'email' additionally creates a read-only web login for the student
    (Cognito `student` group; Cognito emails the temporary password). 400 if
    no matching SnapLogic project exists; 409 if the student already exists
    (registered or graded) — nothing about an existing student is
    overwritten.
    """
    from botocore.exceptions import ClientError

    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    student = str(body.get("student") or "").strip()
    if not student:
        raise BadRequestError("Body must include a non-empty 'student'.")
    email = (_opt_str(body, "email") or "").lower() or None
    if email and not _EMAIL_RE.match(email):
        raise BadRequestError(f"{email!r} does not look like an email address.")
    slug = slugify(student)
    # Resolve the space at registration time so the card (and the dashboard
    # column) always carries the value grading will actually use.
    space = _opt_str(body, "space") or _default_student_space()
    project = _opt_str(body, "project")
    _verify_student_project(project or student, space, _email(claims))
    row = {
        "pk": f"STUDENT#{slug}",
        "sk": "META",
        "entity": "student",
        "slug": slug,
        "display_name": student,
        "space": space,
        "project": project,
        # Present exactly when a login was created — delete_student uses it
        # to know there is a Cognito user to remove.
        "email": email,
        "registered_by": _email(claims),
        "registered_at": utc_now_iso(),
    }
    try:
        dynamo_table().put_item(
            Item=to_dynamo(row), ConditionExpression="attribute_not_exists(pk)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ServiceError(409, f"Student {student!r} is already on the list.")
        raise
    if email:
        # Card first (the duplicate check must win over login creation), then
        # the login; if Cognito refuses, roll the card back so the request
        # fails as a unit and can simply be retried.
        try:
            _create_student_login(student, email)
        except Exception:
            dynamo_table().delete_item(Key={"pk": row["pk"], "sk": "META"})
            raise
    return Response(
        status_code=201,
        content_type="application/json",
        body=json.dumps({"student": public_item(row)}),
    )


@app.post("/v1/gradings")
def post_grading() -> Response:
    """Queue a grade job: everything (default), one 'task', or a 'tasks' subset.

    A scoped run only replaces the selected tasks' results in the stored
    report, appending them if the student was never graded on them before.
    Every run — full or scoped — also refreshes the AI Overall summary from
    the merged report, so the summary never lags the latest verdicts.

    The project space and project name assigned at registration (the
    STUDENT card) dictate where the run looks for the student's pipelines;
    body 'space' overrides the card for one run, and the env default fills
    the gap for cards registered before spaces were stored.
    """
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    student = str(body.get("student") or "").strip()
    if not student:
        raise BadRequestError("Body must include a non-empty 'student'.")
    student_slug = slugify(student)
    card = from_dynamo(
        dynamo_table()
        .get_item(Key={"pk": f"STUDENT#{student_slug}", "sk": "META"})
        .get("Item")
        or {}
    )
    task = (str(body.get("task")).strip() or None) if body.get("task") else None
    tasks: list[str] | None = None
    if body.get("tasks") is not None:
        if task:
            raise BadRequestError("Provide either 'task' or 'tasks', not both.")
        raw = body.get("tasks")
        if not isinstance(raw, list) or not raw:
            raise BadRequestError("'tasks' must be a non-empty array of exercise folders.")
        deduped: list[str] = []
        for entry in raw:
            slug = str(entry or "").strip()
            if not slug:
                raise BadRequestError("'tasks' entries must be non-empty strings.")
            if slug not in deduped:
                deduped.append(slug)
        # A one-element subset IS a single-task grading — collapse it so the
        # worker and report rows keep their existing single-task semantics.
        if len(deduped) == 1:
            task = deduped[0]
        else:
            tasks = deduped
    known = _known_exercise_slugs()
    for slug in ([task] if task else []) + (tasks or []):
        if slug not in known:
            raise BadRequestError(
                f"Unknown exercise folder {slug!r}. Omit 'task'/'tasks' to grade everything."
            )
        _reject_archived(slug, "grade")
    # A full run (no task/tasks) is judged via the 50%-cheaper Message Batches
    # API (asynchronous — the worker polls a batch to completion); a subset or
    # single-task run stays on the instant synchronous path. The worker routes
    # off the scope; `mode` is stored on the job purely for observability.
    is_full_run = not task and not tasks
    payload = {
        "student": student,
        "student_slug": student_slug,
        "space": _opt_str(body, "space") or card.get("space") or _default_student_space(),
        "project": _opt_str(body, "project") or card.get("project"),
        "task": task,
        "tasks": tasks,
        "mode": "batch" if is_full_run else "sync",
    }
    job = _create_job("grade", student_slug, payload, _email(claims))
    return Response(
        status_code=202, content_type="application/json", body=json.dumps(job)
    )


# Hard-gate failures that still route to the AI judge for partial credit
# (points = 10 − Σ deductions). Mirrors evaluator.evaluate._OUTPUT_MISMATCH_GATES;
# kept here so the API doesn't import the SnapLogic-touching evaluate module.
_OUTPUT_MISMATCH_GATES = frozenset({"output_match", "triggered_task_responses_match"})


def _task_is_ai_judged(task: dict[str, Any]) -> bool:
    """True when the task's score came from the AI judge — so its deductions
    and bonus can be edited and points safely recomputed as 10 − Σ deductions.

    False for a MISSING / NEEDS-SYNC task (never reached the AI) and for a
    *procedural* FAIL such as a pipeline-name mismatch, which is fixed at 0
    points with no AI call and an empty deduction list: recomputing 10 − Σ
    there would wrongly hand it full marks.
    """
    if task.get("status") != "evaluated":
        return False
    if task.get("verdict") == "fail":
        gate = task.get("failing_gate")
        if gate and gate not in _OUTPUT_MISMATCH_GATES:
            return False
    return True


def _clean_difference(d: Any) -> dict[str, Any]:
    """Coerce a mentor-supplied difference into the canonical report shape.

    Mirrors evaluator.ai_judge._finalize_evaluation so a hand-edited
    deduction is indistinguishable from an AI-produced one: five known keys,
    points_deducted an int clamped to [0, MAX_POINTS_PER_EXERCISE], and the
    two "source"/"reasoning" fields defaulted rather than left blank.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE

    if not isinstance(d, dict):
        raise BadRequestError("Each difference must be an object.")
    try:
        pts = int(d.get("points_deducted") or 0)
    except (TypeError, ValueError):
        pts = 0
    pts = max(0, min(pts, MAX_POINTS_PER_EXERCISE))
    description = str(d.get("description") or "").strip()
    if not description:
        raise BadRequestError("Each difference needs a non-empty 'description'.")
    return {
        "area": (str(d.get("area") or "").strip() or "(unspecified)"),
        "description": description,
        "points_deducted": pts,
        "rule_source": (str(d.get("rule_source") or "").strip() or "none"),
        "reasoning": str(d.get("reasoning") or "").strip(),
    }


def _derived_points(task: dict[str, Any]) -> int | None:
    """Points a task carries with no manual override in force.

    AI-judged → 10 − Σ deductions (the judge's invariant); a procedural FAIL →
    0; a MISSING / needs-sync task → None (unscored). Used when a mentor clears
    a manual override to fall back to the computed value.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE

    if _task_is_ai_judged(task):
        total = sum(int(d.get("points_deducted") or 0) for d in (task.get("differences") or []))
        return max(0, MAX_POINTS_PER_EXERCISE - total)
    if task.get("verdict") == "fail":
        return 0
    return None


def _audit_text(val: Any, limit: int = 160) -> str | None:
    """A compact snapshot of a text field for the audit log (trimmed)."""
    if val is None:
        return None
    s = str(val).strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or None


def _append_report_audit(
    slug: str, editor: str, when: str, target: str, changes: list[dict[str, Any]]
) -> None:
    """Append one immutable audit row for a report edit; no-op if nothing
    actually changed.

    Rows live under the student's partition as ``AUDIT#<ts>#<rand>`` (sortable
    by time, unique per edit). They deliberately omit the ``entity``/``slug``
    GSI keys so they stay out of the student/exercise list queries — the same
    trick REPORT# rows use (see common.py). None values inside ``changes`` are
    fine here: they are plain attributes, not GSI keys.
    """
    if not changes:
        return
    dynamo_table().put_item(
        Item=to_dynamo(
            {
                "pk": f"STUDENT#{slug}",
                "sk": f"AUDIT#{when}#{uuid.uuid4().hex[:8]}",
                "edited_by": editor,
                "edited_at": when,
                "target": target,
                "changes": changes,
            }
        )
    )


@app.patch("/v1/students/<slug>/report")
def patch_student_report(slug: str) -> dict[str, Any]:
    """Edit a graded report in place (admin or mentor) — no re-grade, no AI cost.

    Report-level key:
      overall_summary          replacement text for the Overall paragraph

    Task-level keys (all need 'task' = the exercise slug; only the ones
    present are applied):
      summary                  replacement summary text
      differences              full replacement list of deductions + notes;
                               each item is {area, description, points_deducted,
                               rule_source, reasoning}. Unless a manual points
                               override is in force, the task's points are
                               recomputed as max(0, 10 - Σ points_deducted) —
                               the same invariant the AI judge uses — and the
                               student's points_earned total is refreshed.
      bonus_question_answer    replacement bonus text, or null/"" to clear it
      points                   direct points OVERRIDE (int 0..10): pins the
                               score, flags the task points_manual=True, and
                               deliberately bypasses 10 − Σ (human judgment
                               wins). null clears the override and falls back to
                               the computed value. Allowed on ANY task — even a
                               MISSING or name-mismatch one — so a mentor can
                               award partial credit; the verdict/status (a
                               hard-gate outcome) is still never changed.

    'differences' and 'bonus_question_answer' apply only to an AI-judged task
    (see _task_is_ai_judged): a MISSING / NEEDS-SYNC task or a procedural FAIL
    (e.g. name mismatch) has a fixed score and no deductions to edit — but its
    points can still be overridden directly.
    Rewrites the latest stored report.json at its existing S3 key (and the
    report.md Overall section for an overall edit); verdicts are never changed.
    Every applied change is appended to an immutable audit log (AUDIT# rows,
    read back via GET .../report/edits). Any of these edits is overwritten by
    the next re-grade of that task, which is the intended semantics: new
    grading, new evaluation.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE, _sum_points

    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}

    new_overall: str | None = None
    if "overall_summary" in body:
        new_overall = str(body.get("overall_summary") or "").strip()
        if not new_overall:
            raise BadRequestError("overall_summary must not be empty.")

    task_slug = str(body.get("task") or "").strip()
    edit_summary = "summary" in body
    edit_diffs = "differences" in body
    edit_bonus = "bonus_question_answer" in body
    edit_points = "points" in body
    if (edit_summary or edit_diffs or edit_bonus or edit_points) and not task_slug:
        raise BadRequestError("Editing a task needs a non-empty 'task' slug.")

    new_summary: str | None = None
    if edit_summary:
        new_summary = str(body.get("summary") or "").strip()
        if not new_summary:
            raise BadRequestError("summary must not be empty.")

    new_diffs: list[dict[str, Any]] | None = None
    if edit_diffs:
        raw_diffs = body.get("differences")
        if not isinstance(raw_diffs, list):
            raise BadRequestError("'differences' must be a list.")
        new_diffs = [_clean_difference(d) for d in raw_diffs]

    new_bonus: str | None = None
    if edit_bonus:
        raw_bonus = body.get("bonus_question_answer")
        # null or "" clears the bonus answer; any other value is stored as text.
        new_bonus = None if raw_bonus is None else (str(raw_bonus).strip() or None)

    # A manual points override: an int pins the score (10 − Σ is bypassed on
    # purpose), null clears the override and falls back to the computed value.
    override_points: int | None = None
    clear_override = False
    if edit_points:
        raw_points = body.get("points")
        if raw_points is None:
            clear_override = True
        else:
            try:
                override_points = int(raw_points)
            except (TypeError, ValueError):
                raise BadRequestError(
                    "points must be an integer 0..10, or null to clear the override."
                )
            override_points = max(0, min(override_points, MAX_POINTS_PER_EXERCISE))

    if new_overall is None and not task_slug:
        raise BadRequestError(
            "Body must include 'overall_summary' and/or a task edit."
        )
    if task_slug and not (edit_summary or edit_diffs or edit_bonus or edit_points):
        raise BadRequestError(
            "A task edit needs 'summary', 'differences', 'bonus_question_answer', "
            "and/or 'points'."
        )

    item = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"}).get("Item")
    if not item:
        raise NotFoundError(f"No graded student {slug!r}.")
    meta = public_item(item)
    # "report_json" is the legacy attribute name (see get_student).
    report_key = meta.get("report_json_key") or meta.get("report_json")
    if not report_key:
        raise BadRequestError("This student has no stored report to edit.")

    s3 = s3_client()
    obj = s3.get_object(Bucket=data_bucket(), Key=str(report_key))
    report = json.loads(obj["Body"].read().decode("utf-8"))
    editor = _email(claims)
    now = utc_now_iso()
    points_changed = False

    if task_slug:
        task = next(
            (t for t in report.get("tasks") or [] if t.get("slug") == task_slug), None
        )
        if task is None:
            raise NotFoundError(f"No task {task_slug!r} in the stored report.")
        # Deductions and the bonus answer only exist for an AI-judged task. A
        # MISSING / NEEDS-SYNC one has no verdict, and a procedural FAIL is
        # fixed at 0 points with no AI call — neither has deductions to edit or
        # a 10 − Σ score to recompute. A direct points OVERRIDE, by contrast, is
        # allowed on any task: human judgment may award partial credit even for
        # a missing or name-mismatch submission.
        if (edit_diffs or edit_bonus) and not _task_is_ai_judged(task):
            raise BadRequestError(
                "Only an AI-judged exercise has deductions or a bonus answer to "
                "edit. A missing or name-mismatch result has a fixed score "
                "(you can still override its points directly)."
            )
        # Snapshot pre-edit values for the audit log.
        before_summary = task.get("summary")
        before_bonus = task.get("bonus_question_answer")
        before_points = task.get("points")
        before_diffs = [dict(d) for d in (task.get("differences") or [])]

        if new_summary is not None:
            task["summary"] = new_summary
            task["summary_edited_by"] = editor
            task["summary_edited_at"] = now
        if edit_diffs:
            task["differences"] = new_diffs
        if edit_bonus:
            task["bonus_question_answer"] = new_bonus

        # Points resolution, in precedence order:
        #   explicit override  → pin the score, flag it manual (10 − Σ bypassed)
        #   clear override     → drop the flag, fall back to the computed value
        #   deductions changed & not manual → recompute 10 − Σ
        #   (deductions changed while manual → points stay pinned)
        if override_points is not None:
            task["points"] = override_points
            task["points_manual"] = True
            points_changed = True
        elif clear_override:
            task.pop("points_manual", None)
            task["points"] = _derived_points(task)
            points_changed = True
        elif edit_diffs and not task.get("points_manual"):
            task["points"] = _derived_points(task)
            points_changed = True

        task["edited_by"] = editor
        task["edited_at"] = now

        # Record only what actually changed (empty → no audit row written).
        task_changes: list[dict[str, Any]] = []
        if new_summary is not None and new_summary != (before_summary or ""):
            task_changes.append(
                {"field": "summary", "from": _audit_text(before_summary),
                 "to": _audit_text(new_summary)}
            )
        if edit_diffs and new_diffs != before_diffs:
            before_ded = sum(int(d.get("points_deducted") or 0) for d in before_diffs)
            after_ded = sum(int(d["points_deducted"]) for d in new_diffs or [])
            task_changes.append(
                {"field": "deductions", "from": f"−{before_ded}", "to": f"−{after_ded}"}
            )
        if edit_bonus and (new_bonus or None) != (before_bonus or None):
            task_changes.append(
                {"field": "bonus", "from": _audit_text(before_bonus),
                 "to": _audit_text(new_bonus)}
            )
        if points_changed and task.get("points") != before_points:
            task_changes.append(
                {"field": "points", "from": before_points, "to": task.get("points")}
            )
        _append_report_audit(slug, editor, now, f"task:{task_slug}", task_changes)

    if new_overall is not None:
        before_overall = report.get("overall_summary")
        report["overall_summary"] = new_overall
        report["overall_summary_edited_by"] = editor
        report["overall_summary_edited_at"] = now
        if new_overall != (before_overall or ""):
            _append_report_audit(
                slug, editor, now, "overall",
                [{"field": "overall_summary", "from": _audit_text(before_overall),
                  "to": _audit_text(new_overall)}],
            )

    if points_changed:
        report["points_earned"] = _sum_points(report.get("tasks") or [])

    s3.put_object(
        Bucket=data_bucket(),
        Key=str(report_key),
        Body=json.dumps(report, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    # Keep the human-readable report.md's Overall paragraph in sync (task
    # sections are left as rendered — the web UI only ever shows report.json).
    if new_overall is not None:
        md_key = meta.get("report_md_key")
        md_text = _s3_text(str(md_key)) if md_key else None
        if md_text is not None:
            from evaluator.runner import _replace_overall_in_md

            s3.put_object(
                Bucket=data_bucket(),
                Key=str(md_key),
                Body=_replace_overall_in_md(md_text, new_overall).encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )

    # Refresh the denormalized student card + stamp the edit.
    update_expr = "SET report_edited_by = :e, report_edited_at = :t"
    values: dict[str, Any] = {":e": editor, ":t": now}
    if new_overall is not None:
        update_expr += ", overall_summary = :s"
        values[":s"] = new_overall
    if points_changed:
        update_expr += ", points_earned = :pe"
        values[":pe"] = report["points_earned"]
    dynamo_table().update_item(
        Key={"pk": f"STUDENT#{slug}", "sk": "META"},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=values,
    )
    meta["report_edited_by"] = editor
    meta["report_edited_at"] = now
    if new_overall is not None:
        meta["overall_summary"] = new_overall
    if points_changed:
        meta["points_earned"] = report["points_earned"]
    # Same shape as GET /v1/students/{slug} so the UI can swap state directly.
    return {"student": meta, "report": report}


@app.get("/v1/students/<slug>/report/edits")
def list_report_edits(slug: str) -> dict[str, Any]:
    """Immutable audit log of every manual edit to a student's report — who
    changed what, when (admin or mentor). Newest first.

    Students never see it: provenance is a mentor/admin concern, and a
    student's own view stays purely their grades (they didn't get the
    'edited by' line either).
    """
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    resp = dynamo_table().query(
        KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}")
        & Key("sk").begins_with("AUDIT#"),
        ScanIndexForward=False,
    )
    return {"edits": [public_item(i) for i in resp.get("Items", [])]}


@app.post("/v1/syncs")
def post_sync() -> Response:
    claims = _require_role(ROLE_ADMIN)  # mentors get 403 here
    body = app.current_event.json_body or {}
    slug = str(body.get("slug") or "").strip()
    target = slug or "all"
    if slug:
        if slug not in _known_exercise_slugs():
            raise BadRequestError(
                f"Unknown exercise folder {slug!r}. Omit 'slug' to sync everything."
            )
        _reject_archived(slug, "sync")
    job = _create_job("sync", target, {"exercise_slug": slug or None}, _email(claims))
    return Response(
        status_code=202, content_type="application/json", body=json.dumps(job)
    )


@app.post("/v1/exercises")
def post_exercise() -> Response:
    """Create a new exercise from the UI (admin only).

    Writes the authored markdown to S3 under exercises/<slug>/ and returns
    presigned PUT URLs for the declared input files — the browser uploads
    those straight to S3 (same 6 MB-ceiling reasoning as the download route,
    in reverse). The next sync job materializes the folder like any other.
    """
    claims = _require_role(ROLE_ADMIN)  # mentors get 403 here
    body = app.current_event.json_body or {}
    slug = str(body.get("slug") or "").strip()
    description_md = str(body.get("description_md") or "")
    notes_md = str(body.get("notes_md") or "")
    raw_resources = body.get("resources") or []

    if not _SLUG_RE.match(slug):
        raise BadRequestError(
            "Folder name must be lowercase letters, digits, '_' or '-', "
            "starting with a letter or digit (e.g. task_07_router_basics)."
        )
    if not description_md.strip():
        raise BadRequestError("description.md content must not be empty.")
    title = _h1_title(description_md)
    if not title:
        raise BadRequestError(
            "description.md must have an H1 heading naming the pipeline "
            "(e.g. '# Task 07 – Router Basics'); sync derives the solution "
            "pipeline lookup from it."
        )
    task_config = _validate_task_config(body.get("task_config"))
    filenames: list[str] = []
    for r in raw_resources:
        _clean_filename((r or {}).get("filename"), label="resource filename", seen=filenames)
    row = _exercise_row(slug)
    # A tombstoned slug (hard-deleted, folder still in the image) may be
    # re-created — the fresh row below simply replaces the tombstone.
    tombstoned = bool(row and row.get("deleted"))
    if slug in _known_exercise_slugs() and not tombstoned:
        raise ServiceError(409, f"Exercise folder {slug!r} already exists.")

    s3 = s3_client()
    prefix = f"{AUTHORED_PREFIX}{slug}/"
    s3.put_object(
        Bucket=data_bucket(),
        Key=f"{prefix}description.md",
        Body=description_md.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    if notes_md.strip():
        s3.put_object(
            Bucket=data_bucket(),
            Key=f"{prefix}notes.md",
            Body=notes_md.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
    row: dict[str, Any] = {
        "pk": f"EXERCISE#{slug}",
        "sk": "META",
        "entity": "exercise",
        "slug": slug,
        "title": title,
        "sync_status": "never_synced",
        "max_points": 10,
        "authored_in": "s3",
        "created_by": _email(claims),
        "created_at": utc_now_iso(),
    }
    if task_config is not None:
        row["task_config"] = task_config
        row["task_config_updated_at"] = row["created_at"]
    dynamo_table().put_item(Item=to_dynamo(row))
    uploads = [
        {
            "filename": name,
            "url": s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": data_bucket(), "Key": f"{prefix}resources/{name}"},
                ExpiresIn=UPLOAD_URL_TTL_SECONDS,
            ),
            "expires_in": UPLOAD_URL_TTL_SECONDS,
        }
        for name in filenames
    ]
    return Response(
        status_code=201,
        content_type="application/json",
        body=json.dumps(
            {
                "exercise": {"slug": slug, "title": title, "sync_status": "never_synced"},
                "uploads": uploads,
            }
        ),
    )


def _image_text(slug: str, filename: str) -> str | None:
    """Authored text from the image copy — fallback for pre-migration folders."""
    from evaluator.config import EXERCISES_DIR

    path = EXERCISES_DIR / slug / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


@app.get("/v1/exercises/<slug>")
def get_exercise(slug: str) -> dict[str, Any]:
    """Full authored content of one exercise — powers the edit dialog."""
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    from evaluator.tasks import list_exercise_folders, list_exercise_resources

    if not _SLUG_RE.match(slug):
        raise NotFoundError(f"No exercise {slug!r}.")
    authored = _scan_authored_s3()
    in_image = slug in list_exercise_folders()
    row = _exercise_row(slug)
    if slug not in authored and not in_image and row is None:
        raise NotFoundError(f"No exercise {slug!r}.")
    if row and row.get("deleted"):
        raise NotFoundError(f"No exercise {slug!r}.")

    meta = {k: v for k, v in (row or {}).items() if k not in ("pk", "sk", "ttl")}
    description_md = _s3_text(f"{AUTHORED_PREFIX}{slug}/description.md")
    notes_md = _s3_text(f"{AUTHORED_PREFIX}{slug}/notes.md")
    if description_md is None and in_image:
        description_md = _image_text(slug, "description.md")
    if notes_md is None and in_image:
        notes_md = _image_text(slug, "notes.md")

    resources = list(authored.get(slug) or [])
    if in_image:
        shipped = {r["filename"] for r in resources}
        resources += [
            r for r in list_exercise_resources(slug) if r["filename"] not in shipped
        ]
    return {
        "exercise": {
            **meta,
            "slug": slug,
            "title": meta.get("title") or _h1_title(description_md or "") or slug,
            "description_md": description_md,
            "notes_md": notes_md,
            "task_config": meta.get("task_config"),
            "resources": resources,
        }
    }


@app.put("/v1/exercises/<slug>")
def put_exercise(slug: str) -> dict[str, Any]:
    """Partial update of one exercise (admin only).

    Accepted keys — only the ones present are applied:
      description_md   rewrite S3 description.md (H1 required; refreshes title)
      notes_md         rewrite S3 notes.md
      task_config      replace the structured config (null = back to auto)
      resources        NEW input files to add — presigned PUT URLs returned
      remove_resources input filenames to delete from S3
      archived         soft-delete flag; archived exercises are excluded from
                       sync/grade jobs and flagged in the UI, nothing is deleted
    """
    claims = _require_role(ROLE_ADMIN)
    body = app.current_event.json_body or {}
    if not _SLUG_RE.match(slug):
        raise NotFoundError(f"No exercise {slug!r}.")
    row = _exercise_row(slug)
    if slug not in _known_exercise_slugs() and row is None:
        raise NotFoundError(f"No exercise {slug!r}.")
    if row and row.get("deleted"):
        raise NotFoundError(f"No exercise {slug!r}.")

    s3 = s3_client()
    prefix = f"{AUTHORED_PREFIX}{slug}/"
    merged: dict[str, Any] = dict(row or {})

    if "description_md" in body:
        description_md = str(body.get("description_md") or "")
        if not description_md.strip():
            raise BadRequestError("description.md content must not be empty.")
        title = _h1_title(description_md)
        if not title:
            raise BadRequestError(
                "description.md must have an H1 heading naming the pipeline."
            )
        s3.put_object(
            Bucket=data_bucket(),
            Key=f"{prefix}description.md",
            Body=description_md.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        merged["title"] = title

    if "notes_md" in body:
        s3.put_object(
            Bucket=data_bucket(),
            Key=f"{prefix}notes.md",
            Body=str(body.get("notes_md") or "").encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )

    if "task_config" in body:
        cfg = _validate_task_config(body.get("task_config"))
        if cfg is None:
            merged.pop("task_config", None)
        else:
            merged["task_config"] = cfg
        merged["task_config_updated_at"] = utc_now_iso()

    if "archived" in body:
        merged["archived"] = bool(body.get("archived"))

    filenames: list[str] = []
    for r in body.get("resources") or []:
        _clean_filename((r or {}).get("filename"), label="resource filename", seen=filenames)
    removals: list[str] = []
    for raw in body.get("remove_resources") or []:
        _clean_filename(raw, label="resource filename", seen=removals)
    for name in removals:
        s3.delete_object(Bucket=data_bucket(), Key=f"{prefix}resources/{name}")

    merged.setdefault("pk", f"EXERCISE#{slug}")
    merged.setdefault("sk", "META")
    merged.setdefault("entity", "exercise")
    merged.setdefault("slug", slug)
    merged.setdefault("sync_status", "never_synced")
    merged.setdefault("max_points", 10)
    merged["updated_by"] = _email(claims)
    merged["updated_at"] = utc_now_iso()
    dynamo_table().put_item(Item=to_dynamo(merged))

    uploads = [
        {
            "filename": name,
            "url": s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": data_bucket(), "Key": f"{prefix}resources/{name}"},
                ExpiresIn=UPLOAD_URL_TTL_SECONDS,
            ),
            "expires_in": UPLOAD_URL_TTL_SECONDS,
        }
        for name in filenames
    ]
    return {"exercise": public_item(merged), "uploads": uploads}


# ---------- hard deletes (admin only) ----------
#
# Deletes are permanent and leave nothing behind on purpose: the data bucket
# is versioned (overwrite insurance), so a hard delete purges every object
# VERSION under the entity's prefixes, not just the current one. What a
# delete cannot reach: CloudWatch log lines (they age out with the group's
# retention) and the nightly exercises-backup/ snapshot in git, which drops
# the exercise on its next run but keeps prior states in git history.


def _purge_s3_prefix(prefix: str) -> int:
    """Permanently delete every object under a prefix — all versions and
    delete markers, so the versioned bucket keeps no recoverable copy."""
    s3 = s3_client()
    bucket = data_bucket()
    deleted = 0
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        targets = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for group in ("Versions", "DeleteMarkers")
            for v in page.get(group, [])
        ]
        for i in range(0, len(targets), 1000):  # delete_objects batch ceiling
            batch = targets[i : i + 1000]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
    return deleted


def _delete_jobs_for_target(job_type: str, target: str) -> int:
    """Drop the JOB rows a deleted entity leaves behind (its job history)."""
    from boto3.dynamodb.conditions import Attr

    table = dynamo_table()
    resp = table.query(
        IndexName="gsi1",
        KeyConditionExpression=Key("entity").eq("job"),
        FilterExpression=Attr("target").eq(target) & Attr("job_type").eq(job_type),
    )
    items = resp.get("Items", [])
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


def _scrub_exercise_from_reports(slug: str) -> int:
    """Remove one exercise's result from every student's live report.

    Rewrites report.json with counts/points recomputed by the same rules a
    grading run uses, drops the task's section from report.md, and refreshes
    the denormalized student card. Older report versions under
    students/<slug>/<version>/ are left alone — they are the students'
    grading history, not the exercise's data.
    """
    from evaluator.grade import (
        MAX_POINTS_PER_EXERCISE,
        _counts_from_tasks,
        _section_matches_slug,
        _split_report_sections,
        _sum_points,
    )

    table = dynamo_table()
    s3 = s3_client()
    resp = table.query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    scrubbed = 0
    for item in resp.get("Items", []):
        meta = from_dynamo(item)
        # "report_json" is the legacy attribute name (see get_student).
        report_key = meta.get("report_json_key") or meta.get("report_json")
        if not report_key:
            continue
        obj = s3.get_object(Bucket=data_bucket(), Key=str(report_key))
        report = json.loads(obj["Body"].read().decode("utf-8"))
        tasks = list(report.get("tasks") or [])
        remaining = [t for t in tasks if t.get("slug") != slug]
        if len(remaining) == len(tasks):
            continue
        counts = _counts_from_tasks(remaining)
        total = sum(counts.values())
        report["tasks"] = remaining
        report["counts"] = {**counts, "total": total}
        report["points_earned"] = _sum_points(remaining)
        report["points_possible"] = total * MAX_POINTS_PER_EXERCISE
        s3.put_object(
            Bucket=data_bucket(),
            Key=str(report_key),
            Body=json.dumps(report, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        md_key = meta.get("report_md_key")
        md_text = _s3_text(str(md_key)) if md_key else None
        if md_text is not None:
            head, sections = _split_report_sections(md_text)
            kept = [s for s in sections if not _section_matches_slug(s, slug)]
            if len(kept) != len(sections):
                merged = (
                    head.rstrip("\n")
                    if not kept
                    else head + "\n\n---\n\n" + "\n\n---\n\n".join(kept).rstrip("\n")
                )
                s3.put_object(
                    Bucket=data_bucket(),
                    Key=str(md_key),
                    Body=(merged + "\n").encode("utf-8"),
                    ContentType="text/markdown; charset=utf-8",
                )
        table.update_item(
            Key={"pk": item["pk"], "sk": "META"},
            UpdateExpression=(
                "SET #c = :c, points_earned = :e, points_possible = :p"
            ),
            ExpressionAttributeNames={"#c": "counts"},
            ExpressionAttributeValues={
                ":c": to_dynamo({**counts, "total": total}),
                ":e": report["points_earned"],
                ":p": report["points_possible"],
            },
        )
        scrubbed += 1
    return scrubbed


@app.delete("/v1/students/<slug>")
def delete_student(slug: str) -> dict[str, Any]:
    """Hard-delete a student and every trace of them (admin only).

    Removes the student card, all REPORT history rows, their grade-job rows,
    the grade lock, every stored report object under students/<slug>/
    (all S3 versions), and — when the registration created one — the
    student's Cognito login. 409 while a grading for them is queued or
    running.
    """
    _require_role(ROLE_ADMIN)
    table = dynamo_table()
    card = table.get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"}).get("Item")
    if not card:
        raise NotFoundError(f"No student {slug!r}.")
    _reject_active_job("grade", slug)

    resp = table.query(KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}"))
    rows = resp.get("Items", [])
    for item in rows:
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    jobs = _delete_jobs_for_target("grade", slug)
    table.delete_item(Key={"pk": lock_key("grade", slug), "sk": "META"})
    objects = _purge_s3_prefix(f"students/{slug}/")
    # An orphaned login could still sign in and read every grade — remove it.
    email = str(card.get("email") or "").strip()
    login_deleted = _delete_student_login(email) if email else False
    return {
        "deleted": {
            "student": slug,
            "rows": len(rows),
            "jobs": jobs,
            "objects": objects,
            "login": login_deleted,
        }
    }


@app.delete("/v1/exercises/<slug>")
def delete_exercise(slug: str) -> dict[str, Any]:
    """Hard-delete an exercise and every trace of it (admin only).

    Removes all its S3 content (authored files, sync artifacts, mirrored
    input files — all versions), the EXERCISE row, its sync-job rows and
    lock, and scrubs its result out of every student's live report. When
    the folder still ships inside the container image, a minimal tombstone
    row (slug + deleted flag) replaces the EXERCISE row — without it the
    image copy would resurrect the exercise on the next listing or sync.
    409 while a sync involving it is queued or running.
    """
    claims = _require_role(ROLE_ADMIN)
    from evaluator.tasks import list_exercise_folders

    if not _SLUG_RE.match(slug):
        raise NotFoundError(f"No exercise {slug!r}.")
    row = _exercise_row(slug)
    if row and row.get("deleted"):
        raise NotFoundError(f"No exercise {slug!r}.")
    in_image = slug in list_exercise_folders()
    if row is None and not in_image and slug not in _scan_authored_s3():
        raise NotFoundError(f"No exercise {slug!r}.")
    _reject_active_job("sync", slug)
    _reject_active_job("sync", "all")
    # Pre-rename in-flight jobs still hold "prep" locks (the worker also still
    # accepts them), so block deletion while one is queued or running too.
    _reject_active_job("prep", slug)
    _reject_active_job("prep", "all")

    objects = _purge_s3_prefix(f"{AUTHORED_PREFIX}{slug}/")
    objects += _purge_s3_prefix(f"exercise-resources/{slug}/")

    table = dynamo_table()
    if in_image:
        table.put_item(
            Item={
                "pk": f"EXERCISE#{slug}",
                "sk": "META",
                "entity": "exercise",
                "slug": slug,
                "deleted": True,
                "deleted_by": _email(claims),
                "deleted_at": utc_now_iso(),
            }
        )
    else:
        table.delete_item(Key={"pk": f"EXERCISE#{slug}", "sk": "META"})
    jobs = _delete_jobs_for_target("sync", slug)
    table.delete_item(Key={"pk": lock_key("sync", slug), "sk": "META"})
    reports = _scrub_exercise_from_reports(slug)
    return {
        "deleted": {
            "exercise": slug,
            "objects": objects,
            "jobs": jobs,
            "reports_scrubbed": reports,
            "tombstoned": in_image,
        }
    }


# ---------- entry point ----------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    source_ip = (
        (event.get("requestContext") or {}).get("http", {}).get("sourceIp", "")
    )
    if not _ip_allowed(source_ip):
        return {
            "statusCode": 403,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Access denied. Your IP address is not whitelisted."}
            ),
        }
    return app.resolve(event, context)
