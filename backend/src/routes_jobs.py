"""Routes: queue a grading or sync job; poll job status; list recent jobs."""
from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools.event_handler import Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
)
from boto3.dynamodb.conditions import Key

from .auth import ROLE_ADMIN, ROLE_MENTOR, _email, _require_role
from .common import dynamo_table, from_dynamo, public_item, query_all, slugify
from .content import _known_exercise_slugs, _reject_archived
from .jobs import _create_job, _get_job
from .resolver import app
from .routes_students import _default_student_space, _opt_str

#: Recent jobs the Activity page shows. Jobs already carry a 90-day TTL, so
#: this cap is about payload size, not retention.
_JOB_LIST_LIMIT = 200


@app.get("/v1/jobs")
def list_jobs() -> dict[str, Any]:
    """Recent grade/sync jobs, newest first — powers the Activity page.

    Reads the sparse gsi1 (entity='job'), sorts by created_at descending in
    Python (the GSI range key is the job id, not a timestamp), and trims to
    the most recent few hundred. Admin/mentor only — students never see the
    job history.
    """
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    items = query_all(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("job")
    )
    jobs = sorted(
        (public_item(i) for i in items),
        key=lambda j: str(j.get("created_at") or ""),
        reverse=True,
    )
    return {"jobs": jobs[:_JOB_LIST_LIMIT]}


@app.get("/v1/gradings/<job_id>")
def get_grading(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)


@app.get("/v1/syncs/<job_id>")
def get_sync(job_id: str) -> dict[str, Any]:
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    return _get_job(job_id)




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


