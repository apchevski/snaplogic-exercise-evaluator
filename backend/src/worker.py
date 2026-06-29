"""Worker Lambda: SQS consumer that executes grade and prep jobs.

One message = one job (batch size 1, reserved concurrency 1, DLQ with
maxReceiveCount 1 — a paid grading run is never auto-retried). Failures are
recorded on the JOB item with a clear message instead of being re-raised, so
the message is consumed and the job surfaces as `failed` in the UI.

Job flow:
    queued (api.py) → running → succeeded | failed

grade jobs:  S3Store.materialize → evaluator.runner.run_grade (hard gates +
             Claude judge + report) → upload report version to S3 → write
             REPORT row + refresh STUDENT card → usage/cost onto the job.
prep jobs:   S3Store.materialize → evaluator.prep sync (slug or all, $0 AI)
             → upload generated artifacts to S3 → survey state into
             EXERCISE rows (powers the Exercises page).
"""
from __future__ import annotations

import json
import os
import traceback
from functools import lru_cache
from typing import Any

from .common import (
    data_bucket,
    dynamo_table,
    from_dynamo,
    lock_key,
    slugify,
    to_dynamo,
    utc_now_iso,
)

# Secret keys copied into the process env (SnapLogic creds + Anthropic key).
_SECRET_ENV_KEYS = (
    "SNAPLOGIC_BASE_URL",
    "SNAPLOGIC_ADMIN_USERNAME",
    "SNAPLOGIC_ADMIN_PASSWORD",
    "SNAPLOGIC_ORG_NAME",
    "SNAPLOGIC_SOLUTION_PROJECT_SPACE",
    "SNAPLOGIC_SOLUTION_PROJECT",
    "SNAPLOGIC_STUDENT_PROJECT_SPACE",
    "ANTHROPIC_API_KEY",
)


@lru_cache(maxsize=1)
def _load_secrets_into_env() -> bool:
    """Fetch the app secret once per container and export the keys."""
    secret_arn = os.environ.get("SECRET_ARN", "").strip()
    if not secret_arn:
        return False  # local/dev: rely on the ambient environment (.env)
    import boto3

    resp = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    data = json.loads(resp["SecretString"])
    for key in _SECRET_ENV_KEYS:
        value = str(data.get(key, "")).strip()
        if value:
            os.environ[key] = value
    return True


def _make_store():
    from evaluator.store import S3Store

    return S3Store(data_bucket())


def _update_job(job_id: str, **attrs: Any) -> None:
    attrs["updated_at"] = utc_now_iso()
    names = {f"#k{i}": k for i, k in enumerate(attrs)}
    values = {f":v{i}": to_dynamo(v) for i, v in enumerate(attrs.values())}
    expr = ", ".join(f"#k{i} = :v{i}" for i in range(len(attrs)))
    dynamo_table().update_item(
        Key={"pk": f"JOB#{job_id}", "sk": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _release_lock(job_type: str, target: str) -> None:
    dynamo_table().delete_item(Key={"pk": lock_key(job_type, target), "sk": "META"})


# ---------- grade ----------


def _run_grade_job(job: dict[str, Any], store: Any) -> dict[str, Any]:
    from evaluator.runner import run_grade

    student = job["student"]
    student_slug = job.get("student_slug") or slugify(student)
    result = run_grade(
        student,
        project_space=job.get("space"),
        task_slug=job.get("task"),
    )

    version = utc_now_iso().replace("+00:00", "Z")
    keys = store.upload_report(student, student_slug, version)
    usage = result.usage.to_dict()
    now = utc_now_iso()

    dynamo_table().put_item(
        Item=to_dynamo(
            {
                "pk": f"STUDENT#{student_slug}",
                "sk": f"REPORT#{version}",
                "version": version,
                "graded_at": now,
                "single_task_only": job.get("task"),
                "counts": result.counts,
                "points_earned": result.points_earned,
                "points_possible": result.points_possible,
                "requested_by": job.get("requested_by"),
                "usage": usage,
                **keys,
            }
        )
    )
    dynamo_table().put_item(
        Item=to_dynamo(
            {
                "pk": f"STUDENT#{student_slug}",
                "sk": "META",
                "entity": "student",
                "slug": student_slug,
                "display_name": student,
                "space": job.get("space"),
                "counts": result.counts,
                "points_earned": result.points_earned,
                "points_possible": result.points_possible,
                "overall_summary": result.report.get("overall_summary"),
                "graded_at": now,
                "latest_version": version,
                "requested_by": job.get("requested_by"),
                **keys,
            }
        )
    )
    return {
        "version": version,
        "counts": result.counts,
        "points_earned": result.points_earned,
        "points_possible": result.points_possible,
        "judged_count": result.judged_count,
        "usage": usage,
        **keys,
    }


# ---------- prep ----------


def _run_prep_job(job: dict[str, Any], store: Any) -> dict[str, Any]:
    from evaluator import prep as prep_mod
    from evaluator.config import load_settings
    from evaluator.snaplogic_client import SnapLogicClient
    from evaluator.tasks import list_exercise_folders, read_pipeline_name_from_description

    slug = job.get("exercise_slug") or None
    rc = prep_mod.cmd_sync(slug, None)
    if rc != 0:
        raise RuntimeError(f"prep sync exited with code {rc}; see the run log.")

    folders = [slug] if slug else list_exercise_folders()
    settings = load_settings()
    now = utc_now_iso()
    survey: list[dict[str, Any]] = []
    with SnapLogicClient(settings) as client:
        for folder in folders:
            report = prep_mod._classify_folder(folder, client, settings)
            uploaded = store.upload_exercise_artifacts(folder)
            dynamo_table().put_item(
                Item=to_dynamo(
                    {
                        "pk": f"EXERCISE#{folder}",
                        "sk": "META",
                        "entity": "exercise",
                        "slug": folder,
                        "title": read_pipeline_name_from_description(folder) or folder,
                        "task_type": report.task_type,
                        "prep_status": report.status,
                        "reason": report.reason,
                        "last_prepped_at": now,
                        "max_points": 10,
                        "artifact_keys": uploaded,
                    }
                )
            )
            survey.append(
                {"slug": folder, "status": report.status, "artifacts": len(uploaded)}
            )
    return {"exercises": survey}


# ---------- dispatch ----------


def _process_job(job: dict[str, Any]) -> None:
    job_id = job["job_id"]
    job_type = job.get("job_type", "")
    target = job.get("target", "")
    _update_job(job_id, status="running", started_at=utc_now_iso())
    try:
        # Lambda's image filesystem is read-only and the React SPA replaces
        # the static dashboard, so never attempt the frontend/dist/index.html rebuild.
        os.environ.setdefault("EVALUATOR_DISABLE_UI_REBUILD", "1")
        _load_secrets_into_env()
        store = _make_store()
        store.materialize_exercises()
        if job_type == "grade":
            result = _run_grade_job(job, store)
        elif job_type == "prep":
            result = _run_prep_job(job, store)
        else:
            raise ValueError(f"Unknown job_type {job_type!r}.")
        _update_job(
            job_id, status="succeeded", finished_at=utc_now_iso(), result=result
        )
    except Exception as e:
        print(f"Job {job_id} failed:\n{traceback.format_exc()}")
        _update_job(
            job_id,
            status="failed",
            finished_at=utc_now_iso(),
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        if job_type and target:
            _release_lock(job_type, target)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    for record in event.get("Records", []):
        job = json.loads(record["body"])
        _process_job(from_dynamo(job))
    return {"ok": True}
