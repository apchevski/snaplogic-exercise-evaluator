"""Routes: the exercise catalog, authoring, input files, hard delete."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aws_lambda_powertools.event_handler import Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceError,
)
from boto3.dynamodb.conditions import Key

from .auth import ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT, _email, _require_role
from .common import (
    data_bucket,
    dynamo_table,
    from_dynamo,
    lock_key,
    public_item,
    query_all,
    s3_client,
    to_dynamo,
    utc_now_iso,
)
from .content import (
    _SLUG_RE,
    AUTHORED_PREFIX,
    UPLOAD_URL_TTL_SECONDS,
    _clean_filename,
    _exercise_row,
    _h1_title,
    _image_text,
    _known_exercise_slugs,
    _normalize_sync_fields,
    _purge_s3_prefix,
    _s3_text,
    _scan_authored_s3,
    _validate_task_config,
)
from .jobs import _delete_jobs_for_target, _reject_active_job
from .resolver import app

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

    items = query_all(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("exercise")
    )
    by_slug = {
        str(i.get("slug")): _normalize_sync_fields(public_item(i))
        for i in items
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


# ---------- analytics (admin / mentor) ----------


@app.get("/v1/analytics/exercises")
def exercise_analytics() -> dict[str, Any]:
    """Per-exercise aggregates across every student's current report.

    For each exercise slug: how many students passed / failed / were missing,
    the average points, and the deduction rules that cost points most often
    (keyed by `rule_source`). Lets an instructor see which exercises or rules
    are tripping the cohort up. Reads each student's live report.json from S3
    (O(students) reads — fine at cohort scale); a student with no report is
    skipped. Admin/mentor only.
    """
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    students = query_all(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    # slug -> tally
    agg: dict[str, dict[str, Any]] = {}

    def bucket(slug: str) -> dict[str, Any]:
        return agg.setdefault(
            slug,
            {
                "slug": slug,
                "graded": 0,
                "pass": 0,
                "fail": 0,
                "missing": 0,
                "points_sum": 0,
                "points_count": 0,
                "deductions": {},  # rule_source -> count
            },
        )

    student_count = 0
    for row in students:
        meta = from_dynamo(row)
        key = meta.get("report_json_key") or meta.get("report_json")
        if not key:
            continue
        try:
            obj = s3_client().get_object(Bucket=data_bucket(), Key=key)
            report = json.loads(obj["Body"].read().decode("utf-8"))
        except Exception:
            continue  # a missing/corrupt report shouldn't sink the whole page
        student_count += 1
        for task in report.get("tasks") or []:
            slug = str(task.get("slug") or "")
            if not slug:
                continue
            b = bucket(slug)
            b["graded"] += 1
            verdict = task.get("verdict") or task.get("status")
            if verdict in ("pass", "fail", "missing"):
                b[verdict] += 1
            pts = task.get("points")
            if isinstance(pts, (int, float)):
                b["points_sum"] += pts
                b["points_count"] += 1
            for d in task.get("differences") or []:
                if int(d.get("points_deducted") or 0) > 0:
                    src = str(d.get("rule_source") or d.get("area") or "unattributed")
                    b["deductions"][src] = b["deductions"].get(src, 0) + 1

    exercises = []
    for slug, b in agg.items():
        top = sorted(b["deductions"].items(), key=lambda kv: kv[1], reverse=True)
        exercises.append(
            {
                "slug": slug,
                "graded": b["graded"],
                "pass": b["pass"],
                "fail": b["fail"],
                "missing": b["missing"],
                "avg_points": (
                    round(b["points_sum"] / b["points_count"], 1)
                    if b["points_count"]
                    else None
                ),
                "top_deductions": [{"rule": r, "count": n} for r, n in top[:5]],
            }
        )
    exercises.sort(key=lambda e: e["slug"])
    return {"students_reported": student_count, "exercises": exercises}


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
    student_items = query_all(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    scrubbed = 0
    for item in student_items:
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


