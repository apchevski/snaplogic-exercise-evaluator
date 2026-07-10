"""Exercise-artifact + report I/O abstraction (local FS for dev, S3 on Lambda).

The split (see .claude/cloud_grading_plan.md):

- **Authored content** (description.md, notes.md, input data files,
  general_evaluation_rules.md, committed task.json) ships inside the Docker
  image — it's in git, CI rebuilds the image on push.
- **Generated artifacts** (solution.json, solution.cache.json, expected/*,
  sync-reconciled task.json) are gitignored and live in S3 under
  ``exercises/<slug>/`` — only sync jobs write them.

On Lambda the image filesystem is read-only, so before a run the
:class:`S3Store` *materializes* a merged exercises directory under ``/tmp``
(env var ``EVALUATOR_EXERCISES_DIR`` points `evaluator.config` there):
authored files copied from the image first, then S3 artifacts downloaded on
top (S3 wins for task.json so sync reconciliation sticks).

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


#: Authored (non-generated) files of an exercise folder, besides resources/.
AUTHORED_FILES = ("description.md", "notes.md")


class LocalStore:
    """Dev store: the repo working tree IS the store."""

    def materialize_exercises(self) -> Path:
        return EXERCISES_DIR

    def materialize_report(self, student: str, report_keys: dict[str, Any]) -> None:
        return None  # grades/<student>/ already lives in the working tree

    def upload_exercise_artifacts(self, slug: str) -> list[str]:
        return []

    def seed_authored_files(self, slug: str) -> list[str]:
        return []

    def upload_report(self, student: str, student_slug: str, version: str) -> dict[str, str]:
        report_dir = GRADES_DIR / student
        return {
            "report_md_key": str(report_dir / "report.md"),
            "report_json_key": str(report_dir / "report.json"),
        }

    # Batch grading persists its scratch across the submit→collect gap. Local
    # dev is one process, so the scratch stays on disk — these are no-ops.
    def upload_scratch(self, job_id: str, student: str) -> int:
        return 0

    def download_scratch(self, job_id: str, student: str) -> int:
        return 0

    def delete_scratch(self, job_id: str) -> None:
        return None


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

        # 2. Generated artifacts from S3 (override the image copy — sync's
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

    def materialize_report(self, student: str, report_keys: dict[str, Any]) -> None:
        """Download a previous report version into GRADES_DIR/<student>/.

        Single-task re-grades merge the new result into the existing
        report.{md,json}; the Lambda filesystem starts empty, so without
        this download the "merge" would silently produce a report holding
        only the regraded task and wipe every other result.
        """
        report_dir = GRADES_DIR / student
        report_dir.mkdir(parents=True, exist_ok=True)
        for label, filename in (
            ("report_md_key", "report.md"),
            ("report_json_key", "report.json"),
        ):
            key = report_keys.get(label)
            if key:
                self._s3.download_file(self.bucket, str(key), str(report_dir / filename))

    # ----- outbound: sync artifacts -----

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

    def seed_authored_files(self, slug: str) -> list[str]:
        """Additively mirror one slug's authored files into S3.

        S3 is the canonical authored store; exercises that still originate
        in the image (git fallback / pre-migration) get their description.md,
        notes.md and resources/* uploaded on their next sync. Never
        overwrites an existing S3 key — a UI edit must not be clobbered by a
        stale image copy — and never deletes anything.
        """
        from botocore.exceptions import ClientError

        slug_dir = EXERCISES_DIR / slug
        candidates: list[tuple[Path, str]] = [
            (slug_dir / name, f"{self.exercises_prefix}{slug}/{name}")
            for name in AUTHORED_FILES
        ]
        resources_dir = slug_dir / "resources"
        if resources_dir.is_dir():
            candidates += [
                (p, f"{self.exercises_prefix}{slug}/resources/{p.name}")
                for p in sorted(resources_dir.iterdir())
                if p.is_file()
            ]
        seeded: list[str] = []
        for path, key in candidates:
            if not path.is_file():
                continue
            try:
                self._s3.head_object(Bucket=self.bucket, Key=key)
                continue  # already authored in S3 — S3 copy wins
            except ClientError:
                pass
            self._s3.upload_file(str(path), self.bucket, key)
            seeded.append(key)
        return seeded

    # ----- batch grading scratch (survives the submit→collect gap) -----
    #
    # A full-run grade job submits an async Message Batch in one Lambda
    # invocation and finalizes the report in a later one, after the batch ends.
    # /tmp is wiped between invocations, so the plan's scratch tree
    # (manifest.json + per-slug ai_context.json/evaluation.json) is stashed in
    # S3 and restored to the SAME /tmp path — grade._resolve_manifest_path
    # anchors on those absolute paths, so the report renderer works unchanged.

    def _scratch_prefix(self, job_id: str) -> str:
        return f"jobs/{job_id}/scratch/"

    def _student_scratch_dir(self, student: str) -> Path:
        return TMP_DIR / "grades" / student

    def upload_scratch(self, job_id: str, student: str) -> int:
        """Stash the student's scratch tree in S3; returns files uploaded.

        The big per-slug ``student/`` pipeline dumps are skipped — cmd_report
        never reads them, so shipping them would only bloat the payload.
        """
        base = self._student_scratch_dir(student)
        prefix = self._scratch_prefix(job_id)
        count = 0
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(base)
            if "student" in rel.parts[1:]:  # skip <slug>/student/*
                continue
            self._s3.upload_file(str(path), self.bucket, prefix + rel.as_posix())
            count += 1
        return count

    def download_scratch(self, job_id: str, student: str) -> int:
        """Restore a stashed scratch tree into the same /tmp path; returns files."""
        base = self._student_scratch_dir(student)
        prefix = self._scratch_prefix(job_id)
        count = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix):]
                if not rel or rel.endswith("/"):
                    continue
                target = base / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                self._s3.download_file(self.bucket, key, str(target))
                count += 1
        return count

    def delete_scratch(self, job_id: str) -> None:
        """Delete the job's stashed scratch prefix after a successful collect."""
        prefix = self._scratch_prefix(job_id)
        paginator = self._s3.get_paginator("list_objects_v2")
        to_delete: list[dict[str, str]] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
                if len(to_delete) == 1000:  # DeleteObjects caps at 1000/call
                    self._s3.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": to_delete}
                    )
                    to_delete = []
        if to_delete:
            self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": to_delete})

    # ----- outbound: grading reports -----

    def upload_report(self, student: str, student_slug: str, version: str) -> dict[str, str]:
        """Upload grades/<student>/report.{md,json} as a new report version."""
        report_dir = GRADES_DIR / student
        keys: dict[str, str] = {}
        # Label names must match what GET /v1/students/<slug> reads off the
        # student META item (report_json_key) — the worker spreads these in.
        for filename, label in (("report.md", "report_md_key"), ("report.json", "report_json_key")):
            path = report_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Expected report file missing: {path}")
            key = f"{self.reports_prefix}{student_slug}/{version}/{filename}"
            self._s3.upload_file(str(path), self.bucket, key)
            keys[label] = key
        return keys
