"""Per-exercise task discovery.

Each exercise lives under `exercises/<slug>/` with a `task.json` file.
Two task types are supported:

CSV-writer exercises (default — back-compat with the original schema):

    {
      "slug": "task_01_generate_csv_report",
      "task_type": "csv_writer",
      "solution_pipeline_path": "Org/PS/Project/Pipeline Name",
      "output_csv_filename": "CA_Birthdays.csv"
    }

Triggered-task exercises (pipeline exposed as a SnapLogic Triggered Task,
exercised over HTTP with one or more scenarios):

    {
      "slug": "task_02_calculator",
      "task_type": "triggered_task",
      "solution_pipeline_path": "Org/PS/Project/Task 02 – Calculator",
      "triggered_task_name": "Task 02 – Calculator Task",
      "requests": [
        {"name": "addition",    "params": {"mathOperation": "3+5"}},
        {"name": "subtraction", "params": {"mathOperation": "10-4"}}
      ]
    }

Adding a new exercise = drop a folder with `description.md`, `notes.md`,
and `task.json`. No Python edits required.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import EXERCISES_DIR

TASK_TYPE_CSV_WRITER = "csv_writer"
TASK_TYPE_TRIGGERED_TASK = "triggered_task"
KNOWN_TASK_TYPES = frozenset({TASK_TYPE_CSV_WRITER, TASK_TYPE_TRIGGERED_TASK})


@dataclass(frozen=True)
class TriggeredRequest:
    name: str
    params: dict[str, str]


@dataclass(frozen=True)
class TaskConfig:
    slug: str
    task_type: str
    solution_pipeline_path: str
    # csv_writer fields
    output_csv_filename: str | None = None
    # triggered_task fields
    triggered_task_name: str | None = None
    requests: tuple[TriggeredRequest, ...] = field(default_factory=tuple)

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
    def expected_dir(self) -> Path:
        return self.dir / "expected"

    @property
    def expected_csv_path(self) -> Path:
        if self.task_type != TASK_TYPE_CSV_WRITER:
            raise AttributeError(
                f"expected_csv_path is only valid for csv_writer tasks "
                f"(slug={self.slug!r}, task_type={self.task_type!r})"
            )
        if self.output_csv_filename is None:
            raise AttributeError(
                f"output_csv_filename is None for csv_writer slug={self.slug!r}"
            )
        return self.expected_dir / self.output_csv_filename

    def expected_response_path(self, request_name: str) -> Path:
        """For triggered_task tasks: the per-scenario JSON file path."""
        if self.task_type != TASK_TYPE_TRIGGERED_TASK:
            raise AttributeError(
                f"expected_response_path is only valid for triggered_task "
                f"(slug={self.slug!r}, task_type={self.task_type!r})"
            )
        return self.expected_dir / f"{request_name}.json"

    @property
    def expected_response_filenames(self) -> tuple[str, ...]:
        """For triggered_task tasks: every expected JSON filename, in scenario order."""
        if self.task_type != TASK_TYPE_TRIGGERED_TASK:
            raise AttributeError(
                f"expected_response_filenames is only valid for triggered_task "
                f"(slug={self.slug!r}, task_type={self.task_type!r})"
            )
        return tuple(f"{r.name}.json" for r in self.requests)

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

    task_type = data.pop("task_type", TASK_TYPE_CSV_WRITER)
    if task_type not in KNOWN_TASK_TYPES:
        raise ValueError(
            f"Unknown task_type {task_type!r} for slug {slug!r}. "
            f"Expected one of: {sorted(KNOWN_TASK_TYPES)}"
        )

    pipeline_path = data.pop("solution_pipeline_path")

    if task_type == TASK_TYPE_CSV_WRITER:
        return TaskConfig(
            slug=slug,
            task_type=task_type,
            solution_pipeline_path=pipeline_path,
            output_csv_filename=data.pop("output_csv_filename"),
        )

    # triggered_task
    task_name = data.pop("triggered_task_name")
    raw_requests = data.pop("requests", [])
    if not isinstance(raw_requests, list) or not raw_requests:
        raise ValueError(
            f"task.json for slug {slug!r} (triggered_task) must have a "
            f"non-empty 'requests' array."
        )
    seen_names: set[str] = set()
    parsed_requests: list[TriggeredRequest] = []
    for r in raw_requests:
        name = r["name"]
        if name in seen_names:
            raise ValueError(
                f"Duplicate request name {name!r} in slug {slug!r}; "
                f"request names must be unique (they're filenames in expected/)."
            )
        seen_names.add(name)
        params = {str(k): str(v) for k, v in (r.get("params") or {}).items()}
        parsed_requests.append(TriggeredRequest(name=name, params=params))

    return TaskConfig(
        slug=slug,
        task_type=task_type,
        solution_pipeline_path=pipeline_path,
        triggered_task_name=task_name,
        requests=tuple(parsed_requests),
    )


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
