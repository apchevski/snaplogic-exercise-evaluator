"""Exercise-artifact + report I/O abstraction (local FS for dev, S3 on Lambda).

The split (see .claude/cloud_grading_plan.md):

- **Authored content** (description.md, notes.md, input data files,
  general_evaluation_rules.md, committed task.json) ships inside the Docker
  image — it's in git, CI rebuilds the image on push.
- **Generated artifacts** (solution.json, solution.cache.json, expected/*,
  prep-reconciled task.json) are gitignored and live in S3 under
  ``exercises/<slug>/`` — only prep jobs write them.

On Lambda the image filesystem is read-only, so before a run the
:class:`S3Store` *materializes* a merged exercises directory under ``/tmp``
(env var ``EVALUATOR_EXERCISES_DIR`` points `evaluator.config` there):
authored files copied from the image first, then S3 artifacts downloaded on
top (S3 wins for task.json so prep reconciliation sticks).

Reports flow the other way: `evaluator.grade` renders into
``EVALUATOR_GRADES_DIR`` and the worker uploads them to
``students/<slug>/<version>/`` — history is kept, every re-grade is a new
version.

:class:`LocalStore` keeps everything as no-ops so the same runner code path
works unchanged in local dev.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import EXERCISES_DIR, GRADES_DIR, REPO_ROOT, TMP_DIR

#: Per-slug generated filenames (expected/ is handled as a whole directory).
GENERATED_ARTIFACT_FILES = ("task.json", "solution.json", "solution.cache.json")


class LocalStore:
    """Dev store: the repo working tree IS the store."""

    def materialize_exercises(self) -> Path:
        return EXERCISES_DIR

    def upload_exercise_artifacts(self, slug: str) -> list[str]:
        return []

    def upload_report(self, student: str, student_slug: str, version: str) -> dict[str, str]:
        report_dir = GRADES_DIR / student
        return {
            "report_md": str(report_dir / "report.md"),
            "report_json": str(report_dir / "report.json"),
        }


class S3Store:
    """Lambda store: merged /tmp exercises dir + S3 for artifacts/reports."""

    def __init__(
        self,
        bucket: str,
        *,
        s3_client: Any | None = None,
        exercises_prefix: str = "exercises/",
        reports_prefix: str = "students/",
        image_exercises_dir: Path | None = None,
    ) -> None:
        if s3_client is None:
            import boto3  # lazy: not a dependency of the local CLI

            s3_client = boto3.client("s3")
        self._s3 = s3_client
        self.bucket = bucket
        self.exercises_prefix = exercises_prefix
        self.reports_prefix = reports_prefix
        # Where the image's read-only authored exercises live. Defaults to
        # the package-relative layout, which inside the Lambda image is
        # /var/task/exercises (or /app/exercises) — distinct from the
        # env-redirected EXERCISES_DIR under /tmp.
        self.image_exercises_dir = image_exercises_dir or (REPO_ROOT / "exercises")

    # ----- inbound: build the merged working dir -----

    def materialize_exercises(self) -> Path:
        dest = EXERCISES_DIR
        dest.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        GRADES_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Authored content from the image.
        if self.image_exercises_dir.resolve() != dest.resolve() and self.image_exercises_dir.is_dir():
            shutil.copytree(self.image_exercises_dir, dest, dirs_exist_ok=True)

        # 2. Generated artifacts from S3 (override the image copy — prep's
        #    reconciled task.json must win over the committed one).
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.exercises_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(self.exercises_prefix):]
                if not rel or rel.endswith("/"):
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                self._s3.download_file(self.bucket, key, str(target))
        return dest

    # ----- outbound: prep artifacts -----

    def upload_exercise_artifacts(self, slug: str) -> list[str]:
        """Upload one slug's generated artifacts from the merged dir to S3."""
        slug_dir = EXERCISES_DIR / slug
        uploaded: list[str] = []
        for name in GENERATED_ARTIFACT_FILES:
            path = slug_dir / name
            if path.is_file():
                key = f"{self.exercises_prefix}{slug}/{name}"
                self._s3.upload_file(str(path), self.bucket, key)
                uploaded.append(key)
        expected_dir = slug_dir / "expected"
        if expected_dir.is_dir():
            for path in sorted(expected_dir.iterdir()):
                if path.is_file():
                    key = f"{self.exercises_prefix}{slug}/expected/{path.name}"
                    self._s3.upload_file(str(path), self.bucket, key)
                    uploaded.append(key)
        return uploaded

    # ----- outbound: grading reports -----

    def upload_report(self, student: str, student_slug: str, version: str) -> dict[str, str]:
        """Upload grades/<student>/report.{md,json} as a new report version."""
        report_dir = GRADES_DIR / student
        keys: dict[str, str] = {}
        for filename, label in (("report.md", "report_md"), ("report.json", "report_json")):
            path = report_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Expected report file missing: {path}")
            key = f"{self.reports_prefix}{student_slug}/{version}/{filename}"
            self._s3.upload_file(str(path), self.bucket, key)
            keys[label] = key
        return keys
