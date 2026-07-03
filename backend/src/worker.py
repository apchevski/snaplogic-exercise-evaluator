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
import shutil
import traceback
from functools import lru_cache
from typing import Any

from boto3.dynamodb.conditions import Key

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


# ---------- exercise rows (authored state lives in DynamoDB) ----------

#: EXERCISE-row attributes the prep survey must carry forward — they're
#: authored via the API (create/edit dialog), and the survey's put_item
#: would otherwise wipe them.
_PRESERVED_EXERCISE_FIELDS = (
    "task_config",
    "task_config_updated_at",
    "archived",
    "authored_in",
    "created_by",
    "created_at",
    "updated_by",
    "updated_at",
)


def _exercise_rows() -> dict[str, dict[str, Any]]:
    resp = dynamo_table().query(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("exercise")
    )
    return {str(i.get("slug")): from_dynamo(i) for i in resp.get("Items", [])}


def _prune_archived_exercises(rows: dict[str, dict[str, Any]]) -> list[str]:
    """Drop archived exercises from the merged /tmp tree (S3 is untouched).

    Runs after materialize for BOTH job types: prep skips them, and grading
    no longer counts them toward the points denominator.
    """
    from evaluator.config import EXERCISES_DIR

    pruned = []
    for slug, row in rows.items():
        if row.get("archived") and (EXERCISES_DIR / slug).is_dir():
            shutil.rmtree(EXERCISES_DIR / slug)
            pruned.append(slug)
    return pruned


def _synthesize_task_json(folder: str, cfg: dict[str, Any]) -> None:
    """Write task.json into the merged tree from the exercise's task_config.

    The config (authored in the UI dialog) is env-neutral; the one
    env-specific field, solution_pipeline_path, is derived here from the
    SnapLogic settings + the description.md H1 — the same rule prep's
    reconciler uses. Overwrites whatever task.json the overlay produced:
    the stored config is canonical, and prep re-uploads the result to S3.
    """
    from evaluator.config import EXERCISES_DIR, load_settings
    from evaluator.tasks import read_pipeline_name_from_description

    pipeline_name = read_pipeline_name_from_description(folder)
    if not pipeline_name:
        return  # prep's classify step will surface missing_description
    settings = load_settings()
    data: dict[str, Any] = {
        "task_type": cfg["task_type"],
        "solution_pipeline_path": (
            f"{settings.org_name}/{settings.project_space_name}/"
            f"{settings.project_name}/{pipeline_name}"
        ),
    }
    if cfg["task_type"] == "file_writer":
        names = [str(n) for n in cfg["output_filenames"]]
        if len(names) == 1:
            data["output_filename"] = names[0]
        else:
            data["output_filenames"] = names
        if cfg.get("output_match_mode", "exact") != "exact":
            data["output_match_mode"] = cfg["output_match_mode"]
    else:  # triggered_task
        data["triggered_task_name"] = cfg["triggered_task_name"]
        data["requests"] = [
            {"name": r["name"], "params": dict(r.get("params") or {})}
            for r in cfg["requests"]
        ]
    path = EXERCISES_DIR / folder / "task.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------- grade ----------


def _run_grade_job(job: dict[str, Any], store: Any) -> dict[str, Any]:
    from evaluator.runner import run_grade

    student = job["student"]
    student_slug = job.get("student_slug") or slugify(student)
    if job.get("task"):
        # Single-task re-grade merges into the previous report, which on
        # Lambda must be pulled from S3 first (fresh /tmp every job).
        meta = (
            dynamo_table()
            .get_item(Key={"pk": f"STUDENT#{student_slug}", "sk": "META"})
            .get("Item")
            or {}
        )
        store.materialize_report(
            student,
            {
                "report_md_key": meta.get("report_md_key"),
                # "report_json" is the legacy attribute name (see api.py).
                "report_json_key": meta.get("report_json_key") or meta.get("report_json"),
            },
        )
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


def _run_prep_job(
    job: dict[str, Any], store: Any, rows: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    from evaluator import prep as prep_mod
    from evaluator.config import load_settings
    from evaluator.snaplogic_client import SnapLogicClient
    from evaluator.tasks import list_exercise_folders, read_pipeline_name_from_description

    slug = job.get("exercise_slug") or None
    folders = [slug] if slug else list_exercise_folders()

    # UI-authored type config is canonical: synthesize task.json from it
    # before sync so the reconciler refreshes solution + expected/ off it.
    for folder in folders:
        cfg = (rows.get(folder) or {}).get("task_config")
        if cfg:
            _synthesize_task_json(folder, cfg)

    rc = prep_mod.cmd_sync(slug, None)
    if rc != 0:
        raise RuntimeError(f"prep sync exited with code {rc}; see the run log.")

    settings = load_settings()
    now = utc_now_iso()
    survey: list[dict[str, Any]] = []
    with SnapLogicClient(settings) as client:
        for folder in folders:
            report = prep_mod._classify_folder(folder, client, settings)
            uploaded = store.upload_exercise_artifacts(folder)
            # Migration path: image-shipped authored files (git fallback /
            # pre-pivot exercises) graduate to S3 here, additively.
            seeded = store.seed_authored_files(folder)
            existing = rows.get(folder) or {}
            preserved = {
                k: existing[k] for k in _PRESERVED_EXERCISE_FIELDS if k in existing
            }
            dynamo_table().put_item(
                Item=to_dynamo(
                    {
                        **preserved,
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
                {
                    "slug": folder,
                    "status": report.status,
                    "artifacts": len(uploaded),
                    "seeded": len(seeded),
                }
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
        # Archived exercises stay in S3 (nothing is ever deleted there) but
        # are dropped from the working tree, so prep skips them and grading
        # stops counting them toward the points denominator.
        rows = _exercise_rows()
        _prune_archived_exercises(rows)
        if job_type == "grade":
            result = _run_grade_job(job, store)
        elif job_type == "prep":
            result = _run_prep_job(job, store, rows)
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
