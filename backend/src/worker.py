"""Worker Lambda: SQS consumer that executes grade and sync jobs.

One message = one job (batch size 1, reserved concurrency 1, DLQ with
maxReceiveCount 1 — a paid grading run is never auto-retried). Failures are
recorded on the JOB item with a clear message instead of being re-raised, so
the message is consumed and the job surfaces as `failed` in the UI.

Job flow:
    queued (api.py) → running → succeeded | failed

grade jobs:  S3Store.materialize → evaluator.runner.run_grade (hard gates +
             Claude judge + report) → upload report version to S3 → write
             REPORT row + refresh STUDENT card → usage/cost onto the job.
sync jobs:   S3Store.materialize → evaluator.sync sync (slug or all, $0 AI)
             → upload generated artifacts to S3 → survey state into
             EXERCISE rows (powers the Exercises page).
"""
from __future__ import annotations

import json
import os
import shutil
import traceback
from typing import Any

from boto3.dynamodb.conditions import Key

from .common import (
    LOCK_TTL_SECONDS,
    apply_user_overrides,
    data_bucket,
    dynamo_table,
    epoch_in,
    from_dynamo,
    lock_key,
    slugify,
    sqs_client,
    to_dynamo,
    utc_now_iso,
)


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

#: EXERCISE-row attributes the sync survey must carry forward — they're
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


def _prune_excluded_exercises(rows: dict[str, dict[str, Any]]) -> list[str]:
    """Drop archived and hard-deleted exercises from the merged /tmp tree.

    Runs after materialize for BOTH job types: sync skips them (so a deleted
    exercise's image copy is never re-seeded into S3), and grading no longer
    counts them toward the points denominator. Archived exercises keep their
    S3 content; deleted ones only exist as an image folder plus the tombstone
    row that carries the `deleted` flag (see api.delete_exercise).
    """
    from evaluator.config import EXERCISES_DIR

    pruned = []
    for slug, row in rows.items():
        if (row.get("archived") or row.get("deleted")) and (EXERCISES_DIR / slug).is_dir():
            shutil.rmtree(EXERCISES_DIR / slug)
            pruned.append(slug)
    return pruned


def _synthesize_task_json(folder: str, cfg: dict[str, Any]) -> None:
    """Write task.json into the merged tree from the exercise's task_config.

    The config (authored in the UI dialog) is env-neutral; the one
    env-specific field, solution_pipeline_path, is derived here from the
    SnapLogic settings + the description.md H1 — the same rule sync's
    reconciler uses. Overwrites whatever task.json the overlay produced:
    the stored config is canonical, and sync re-uploads the result to S3.
    """
    from evaluator.config import EXERCISES_DIR, load_settings
    from evaluator.tasks import read_pipeline_name_from_description

    pipeline_name = read_pipeline_name_from_description(folder)
    if not pipeline_name:
        return  # sync's classify step will surface missing_description
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


def _merge_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine per-run usage dicts (multi-task jobs): sum numbers, keep labels."""
    if len(usages) == 1:
        return usages[0]
    merged: dict[str, Any] = {}
    for u in usages:
        for k, v in (u or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                merged[k] = merged.get(k, 0) + v
            else:
                merged.setdefault(k, v)
    return merged


# Full "grade all" runs judge every exercise through the Message Batches API
# (50% cheaper) — asynchronous, so the job is split across two worker
# invocations. These bound the follow-up polling.
_BATCH_POLL_DELAY_SECONDS = 60
_BATCH_MAX_POLLS = 120  # ~2h at 60s — well past the "usually <1h" batch window


def _student_meta(student_slug: str) -> dict[str, Any]:
    return from_dynamo(
        dynamo_table()
        .get_item(Key={"pk": f"STUDENT#{student_slug}", "sk": "META"})
        .get("Item")
        or {}
    )


def _enqueue_delayed(body: dict[str, Any], delay_seconds: int = _BATCH_POLL_DELAY_SECONDS) -> None:
    """Re-enqueue a message to our own queue, invisible until the timer fires.

    Used to poll an in-flight grading batch without occupying the worker while
    it waits — the delayed message sits hidden in SQS, so other jobs run
    meanwhile.
    """
    sqs_client().send_message(
        QueueUrl=os.environ["QUEUE_URL"],
        DelaySeconds=delay_seconds,
        MessageBody=json.dumps(body),
    )


def _refresh_lock_ttl(job_type: str, target: str) -> None:
    """Push out the per-target lock's TTL so a >30-min batch keeps its lock."""
    dynamo_table().update_item(
        Key={"pk": lock_key(job_type, target), "sk": "META"},
        UpdateExpression="SET #ttl = :ttl",
        ExpressionAttributeNames={"#ttl": "ttl"},
        ExpressionAttributeValues={":ttl": to_dynamo(epoch_in(LOCK_TTL_SECONDS))},
    )


def _collect_message(job: dict[str, Any], poll_attempts: int) -> dict[str, Any]:
    """Build the 'check the batch again later' message for a grade collect."""
    return {
        "job_id": job["job_id"],
        "job_type": "grade",
        "target": job.get("target", ""),
        "phase": "collect",
        "batch_id": job["batch_id"],
        "student": job["student"],
        "student_slug": job.get("student_slug") or slugify(job["student"]),
        "space": job.get("space"),
        "project": job.get("project"),
        "requested_by": job.get("requested_by"),
        "poll_attempts": poll_attempts,
    }


def _finalize_grade_rows(
    store: Any,
    job: dict[str, Any],
    student: str,
    student_slug: str,
    space: Any,
    project: Any,
    meta: dict[str, Any],
    results: list[Any],
    scope: list[str] | None,
) -> dict[str, Any]:
    """Upload the report version and write the REPORT + STUDENT rows.

    Shared by the synchronous path (`_run_grade_job`) and the batch paths
    (submit-fast-path and collect), so all three write identical rows. Each
    result's discounted `usage` and any full-price `overall_usage` are summed.
    """
    result = results[-1]
    version = utc_now_iso().replace("+00:00", "Z")
    keys = store.upload_report(student, student_slug, version)
    usage_dicts: list[dict[str, Any]] = []
    for r in results:
        usage_dicts.append(r.usage.to_dict())
        if getattr(r, "overall_usage", None) is not None:
            usage_dicts.append(r.overall_usage.to_dict())
    usage = _merge_usage(usage_dicts)
    judged_count = sum(r.judged_count for r in results)
    now = utc_now_iso()

    report_row = {
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
    if scope is not None and len(scope) > 1:
        report_row["tasks_scope"] = scope
    dynamo_table().put_item(Item=to_dynamo(report_row))
    student_row = {
        "pk": f"STUDENT#{student_slug}",
        "sk": "META",
        "entity": "student",
        "slug": student_slug,
        "display_name": student,
        "space": space,
        "project": project,
        "counts": result.counts,
        "points_earned": result.points_earned,
        "points_possible": result.points_possible,
        "overall_summary": result.report.get("overall_summary"),
        "graded_at": now,
        "latest_version": version,
        "requested_by": job.get("requested_by"),
        **keys,
    }
    # Registration fields survive the card refresh (see POST /v1/students).
    # "email" marks the student's web login — losing it on a regrade would
    # orphan the Cognito user when the student is later deleted.
    for carry in ("registered_by", "registered_at", "email"):
        if meta.get(carry):
            student_row[carry] = meta[carry]
    dynamo_table().put_item(Item=to_dynamo(student_row))
    return {
        "version": version,
        "counts": result.counts,
        "points_earned": result.points_earned,
        "points_possible": result.points_possible,
        "judged_count": judged_count,
        "usage": usage,
        **keys,
    }


def _run_grade_job(job: dict[str, Any], store: Any) -> dict[str, Any]:
    """Synchronous grading: a subset selection or a single-task regrade.

    Full "grade all" runs go through the batch path instead (see
    `_submit_grade_batch_job`); this stays for the instant, scoped runs.
    """
    from evaluator.runner import run_grade

    student = job["student"]
    student_slug = job.get("student_slug") or slugify(student)
    # Scope: None = full grading; otherwise the exercise slugs to (re)grade.
    scope: list[str] | None = None
    if job.get("tasks"):
        scope = [str(t) for t in job["tasks"]]
    elif job.get("task"):
        scope = [str(job["task"])]
    meta = _student_meta(student_slug)
    if scope:
        # Scoped runs merge into the previous report, which on Lambda must
        # be pulled from S3 first (fresh /tmp every job). A never-graded
        # student has no keys — nothing downloads and a fresh report grows
        # task by task.
        store.materialize_report(
            student,
            {
                "report_md_key": meta.get("report_md_key"),
                # "report_json" is the legacy attribute name (see api.py).
                "report_json_key": meta.get("report_json_key") or meta.get("report_json"),
            },
        )
    # The job payload carries the resolved space/project (API merges body →
    # STUDENT card → env default); the card is the fallback for jobs queued
    # before that resolution existed.
    space = job.get("space") or meta.get("space")
    project = job.get("project") or meta.get("project")
    if scope is None:
        results = [
            run_grade(student, project_space=space, project=project, task_slug=None)
        ]
    else:
        # One run per slug; each merges into report.{md,json} on disk, so
        # the last result carries the accumulated counts and points.
        results = [
            run_grade(student, project_space=space, project=project, task_slug=slug)
            for slug in scope
        ]
    return _finalize_grade_rows(
        store, job, student, student_slug, space, project, meta, results, scope
    )


def _submit_grade_batch_job(job: dict[str, Any], store: Any) -> dict[str, Any]:
    """Phase 1 of a full run: plan, fire the AI-judging batch, hand off.

    Returns ``{"done": True, "result": <row dict>}`` when the plan found
    nothing to judge (report rendered synchronously), or ``{"done": False}``
    when a batch is in flight (status set to ``batch_processing`` and a delayed
    collect message enqueued — the collect step now owns the lock).
    """
    from evaluator.ai_judge import AIJudge
    from evaluator.runner import submit_grade_batch

    student = job["student"]
    student_slug = job.get("student_slug") or slugify(student)
    meta = _student_meta(student_slug)
    space = job.get("space") or meta.get("space")
    project = job.get("project") or meta.get("project")
    job_id = job["job_id"]

    outcome = submit_grade_batch(
        student,
        project_space=space,
        project=project,
        judge=AIJudge(),
        store=store,
        job_id=job_id,
    )
    if outcome["done"]:
        row = _finalize_grade_rows(
            store, job, student, student_slug, space, project, meta,
            [outcome["result"]], None,
        )
        return {"done": True, "result": row}

    batch_id = outcome["batch_id"]
    _update_job(
        job_id,
        status="batch_processing",
        batch_id=batch_id,
        poll_attempts=0,
        judged_count=outcome["judged_count"],
    )
    _enqueue_delayed(
        {
            "job_id": job_id,
            "job_type": "grade",
            "target": job.get("target", ""),
            "phase": "collect",
            "batch_id": batch_id,
            "student": student,
            "student_slug": student_slug,
            "space": space,
            "project": project,
            "requested_by": job.get("requested_by"),
            "poll_attempts": 0,
        }
    )
    return {"done": False}


def _process_grade_collect(job: dict[str, Any]) -> None:
    """Phase 2 of a full run: poll the batch; finalize once it has ended.

    Runs on the delayed collect message. Still-processing → refresh the lock,
    bump the counter, re-enqueue. Ended → read results, render the report,
    write rows, release the lock, drop the S3 scratch. Reading a finished batch
    is free, so a transient error re-enqueues (bounded) instead of failing a
    grade whose paid batch already succeeded.
    """
    from evaluator.ai_judge import AIJudge
    from evaluator.runner import batch_status, collect_grade_batch

    job_id = job["job_id"]
    target = job.get("target", "")
    student = job["student"]
    student_slug = job.get("student_slug") or slugify(student)
    batch_id = job["batch_id"]
    attempts = int(job.get("poll_attempts") or 0)

    try:
        os.environ.setdefault("EVALUATOR_DISABLE_UI_REBUILD", "1")
        # The collect step must judge/price under the same credentials the
        # submit ran with — the requester's own, when they stored any.
        apply_user_overrides(job.get("requested_by"))
        judge = AIJudge()

        if batch_status(batch_id, judge=judge) != "ended":
            if attempts >= _BATCH_MAX_POLLS:
                _update_job(
                    job_id,
                    status="failed",
                    finished_at=utc_now_iso(),
                    error=f"Grading batch {batch_id} did not finish within the poll window.",
                )
                _release_lock("grade", target)
                return
            _refresh_lock_ttl("grade", target)
            _update_job(job_id, poll_attempts=attempts + 1)
            _enqueue_delayed(_collect_message(job, attempts + 1))
            return

        store = _make_store()
        meta = _student_meta(student_slug)
        space = job.get("space") or meta.get("space")
        project = job.get("project") or meta.get("project")
        result = collect_grade_batch(
            student,
            project_space=space,
            batch_id=batch_id,
            judge=judge,
            store=store,
            job_id=job_id,
        )
        row = _finalize_grade_rows(
            store, job, student, student_slug, space, project, meta, [result], None
        )
        _update_job(job_id, status="succeeded", finished_at=utc_now_iso(), result=row)
        _release_lock("grade", target)
        store.delete_scratch(job_id)
    except Exception as e:
        print(f"Grade collect {job_id} failed:\n{traceback.format_exc()}")
        if attempts < _BATCH_MAX_POLLS:
            try:  # transient — re-poll rather than dead-letter a paid grade
                _refresh_lock_ttl("grade", target)
                _update_job(job_id, poll_attempts=attempts + 1)
                _enqueue_delayed(_collect_message(job, attempts + 1))
                return
            except Exception:
                pass
        _update_job(
            job_id,
            status="failed",
            finished_at=utc_now_iso(),
            error=f"{type(e).__name__}: {e}",
        )
        _release_lock("grade", target)


# ---------- sync ----------


def _run_sync_job(
    job: dict[str, Any], store: Any, rows: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    from evaluator import sync as sync_mod
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

    rc = sync_mod.cmd_sync(slug, None)
    if rc != 0:
        raise RuntimeError(f"sync exited with code {rc}; see the run log.")

    settings = load_settings()
    now = utc_now_iso()
    survey: list[dict[str, Any]] = []
    with SnapLogicClient(settings) as client:
        for folder in folders:
            report = sync_mod._classify_folder(folder, client, settings)
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
                        "sync_status": report.status,
                        "reason": report.reason,
                        "last_synced_at": now,
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

    # A grade batch "collect" is a follow-up poll, not a fresh job: it manages
    # its own status/lock lifecycle and needs no exercise materialize (it reads
    # the stashed scratch + Claude only), so handle it before that expensive step.
    if job_type == "grade" and job.get("phase") == "collect":
        _process_grade_collect(job)
        return

    _update_job(job_id, status="running", started_at=utc_now_iso())
    release_lock = True
    try:
        # Lambda's image filesystem is read-only and the React SPA replaces
        # the static dashboard, so never attempt the frontend/dist/index.html rebuild.
        os.environ.setdefault("EVALUATOR_DISABLE_UI_REBUILD", "1")
        # Shared secret first, then the requester's own credentials on top
        # (their SnapLogic login, Anthropic key, and judge model, when stored).
        apply_user_overrides(job.get("requested_by"))
        store = _make_store()
        store.materialize_exercises()
        # Archived and hard-deleted exercises are dropped from the working
        # tree, so sync skips them (and never re-seeds a deleted one) and
        # grading stops counting them toward the points denominator.
        rows = _exercise_rows()
        _prune_excluded_exercises(rows)
        if job_type == "grade":
            # Full "grade all" (no task/tasks) → asynchronous 50%-cheaper batch.
            # A subset or single-task regrade stays on the instant sync path.
            if not job.get("task") and not job.get("tasks"):
                outcome = _submit_grade_batch_job(job, store)
                if not outcome["done"]:
                    # A batch is in flight; the collect step owns the lock and
                    # sets the final status. Nothing more to do here.
                    release_lock = False
                    return
                result = outcome["result"]
            else:
                result = _run_grade_job(job, store)
        elif job_type in ("sync", "prep"):  # "prep" = pre-rename in-flight jobs
            result = _run_sync_job(job, store, rows)
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
        if release_lock and job_type and target:
            _release_lock(job_type, target)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    for record in event.get("Records", []):
        job = json.loads(record["body"])
        _process_job(from_dynamo(job))
    return {"ok": True}
