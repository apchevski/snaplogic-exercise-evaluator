"""Per-exercise task discovery.

Each exercise lives under `exercises/<slug>/` with a `task.json` file:

    {
      "slug": "task_01_generate_csv_report",
      "solution_pipeline_path": "Org/PS/Project/Pipeline Name",
      "output_csv_filename": "CA_Birthdays.csv"
    }

Adding a new exercise = drop a folder with `description.md`, `notes.md`,
and `task.json`. No Python edits required.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import EXERCISES_DIR


@dataclass(frozen=True)
class TaskConfig:
    slug: str
    solution_pipeline_path: str
    output_csv_filename: str

    @property
    def dir(self) -> Path:
        return EXERCISES_DIR / self.slug

    @property
    def description_path(self) -> Path:
        return self.dir / "description.md"

    @property
    def task_notes_path(self) -> Path:
        return self.dir / "notes.md"

    @property
    def expected_csv_path(self) -> Path:
        return self.dir / "expected" / self.output_csv_filename

    @property
    def solution_json_path(self) -> Path:
        return self.dir / "solution.json"

    @property
    def solution_cache_sidecar_path(self) -> Path:
        return self.dir / "solution.cache.json"


def load_task(slug: str) -> TaskConfig:
    cfg_path = EXERCISES_DIR / slug / "task.json"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No task.json for slug {slug!r} at {cfg_path}. "
            f"Known slugs: {list_tasks()}"
        )
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    # task.json's `slug` field is optional; folder name is the source of truth.
    data.pop("slug", None)
    return TaskConfig(slug=slug, **data)


def list_tasks() -> list[str]:
    """Return all exercise slugs that have a task.json."""
    if not EXERCISES_DIR.exists():
        return []
    return sorted(
        p.parent.name
        for p in EXERCISES_DIR.glob("*/task.json")
        if p.is_file()
    )


def list_exercise_folders() -> list[str]:
    """Return every child folder under exercises/, regardless of task.json.

    Used by /grade and /prep to discover folders that exist on disk but
    haven't been registered yet (no task.json). list_tasks() filters
    those out; this one doesn't.
    """
    if not EXERCISES_DIR.exists():
        return []
    return sorted(
        p.name
        for p in EXERCISES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def read_pipeline_name_from_description(folder: str) -> str | None:
    """Return the canonical pipeline name for an exercise.

    The pipeline name lives in the first H1 heading of
    `exercises/<folder>/description.md` (e.g. "# Task 01 – Generate CSV
    Report"). This is what the student's pipeline must be named and what
    /prep uses to look up the solution pipeline in SnapLogic. Returns
    None if description.md is missing or has no H1 heading. Folder slugs
    can stay snake_case; the pipeline name lives in description.md.
    """
    desc = EXERCISES_DIR / folder / "description.md"
    if not desc.exists():
        return None
    for raw in desc.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip() or None
    return None
