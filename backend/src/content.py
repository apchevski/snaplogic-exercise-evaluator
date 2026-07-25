"""S3 + DynamoDB content helpers shared across resources.

Mostly the authored-exercise store (S3 is the source of truth for exercise
content -- descriptions, notes, task_config, resources), plus the generic
S3 text-read and prefix-purge helpers the report/delete routes reuse.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
)

from .common import (
    data_bucket,
    dynamo_table,
    from_dynamo,
    s3_client,
)

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




def _image_text(slug: str, filename: str) -> str | None:
    """Authored text from the image copy — fallback for pre-migration folders."""
    from evaluator.config import EXERCISES_DIR

    path = EXERCISES_DIR / slug / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")




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


