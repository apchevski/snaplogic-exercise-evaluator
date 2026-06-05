"""Per-exercise task discovery.

Each exercise lives under `exercises/<slug>/` with a `task.json` file.
Two task types are supported:

File-writer exercises (the solution pipeline writes one or more output
files via binary-write snaps; the file format is incidental — the
comparison gate handles CSV and XLSX):

    {
      "slug": "task_01_generate_csv_report",
      "task_type": "file_writer",
      "solution_pipeline_path": "Org/PS/Project/Pipeline Name",
      "output_filename": "CA_Birthdays.csv"
    }

A file_writer pipeline that writes more than one file lists them all
under `output_filenames` (an array) instead of `output_filename`. Every
listed file must match for the exercise to PASS:

    {
      "slug": "task_05_multiple_flows_one_pipeline",
      "task_type": "file_writer",
      "solution_pipeline_path": "Org/PS/Project/Pipeline Name",
      "output_filenames": ["Report1.csv", "Report2.csv", "Report3.csv"]
    }

Back-compat: this task type and its filename keys were originally named
after CSV (the first exercises all wrote CSVs). The old names are still
accepted in task.json — `task_type: "csv_writer"` is an alias for
`file_writer`, and `output_csv_filename` / `output_csv_filenames` are
aliases for `output_filename` / `output_filenames`.

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

TASK_TYPE_FILE_WRITER = "file_writer"
TASK_TYPE_TRIGGERED_TASK = "triggered_task"
KNOWN_TASK_TYPES = frozenset({TASK_TYPE_FILE_WRITER, TASK_TYPE_TRIGGERED_TASK})

# Back-compat: the original schema called the file-writer type "csv_writer"
# (the first exercises all wrote CSVs). It's normalized to the format-neutral
# canonical name on load, so old task.json files keep working unchanged.
TASK_TYPE_ALIASES = {"csv_writer": TASK_TYPE_FILE_WRITER}

# task.json output-filename keys. Each pair is (canonical, legacy-alias); the
# legacy "_csv_" keys are still accepted but normalize to the canonical name.
_SINGULAR_OUTPUT_KEYS = ("output_filename", "output_csv_filename")
_PLURAL_OUTPUT_KEYS = ("output_filenames", "output_csv_filenames")

# How the file_writer output gate compares the student file to the solution.
#   "exact"        — header + row multiset must match (the default).
#   "columns_only" — only the column header is compared; row data is
#                    ignored. For exercises whose output is inherently
#                    non-deterministic (e.g. an API that returns random
#                    data every run), so the rows can never match but the
#                    column schema still must. See task_04_born_on_friday.
OUTPUT_MATCH_EXACT = "exact"
OUTPUT_MATCH_COLUMNS_ONLY = "columns_only"
KNOWN_OUTPUT_MATCH_MODES = frozenset({OUTPUT_MATCH_EXACT, OUTPUT_MATCH_COLUMNS_ONLY})


@dataclass(frozen=True)
class TriggeredRequest:
    name: str
    params: dict[str, str]


@dataclass(frozen=True)
class TaskConfig:
    slug: str
    task_type: str
    solution_pipeline_path: str
    # file_writer fields — one or more output files; a single-output exercise
    # is just a length-1 tuple (back-compat with the `output_filename` key).
    output_filenames: tuple[str, ...] = ()
    output_match_mode: str = OUTPUT_MATCH_EXACT
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
    def expected_output_paths(self) -> list[Path]:
        """Cached expected output file paths, one per registered filename."""
        if self.task_type != TASK_TYPE_FILE_WRITER:
            raise AttributeError(
                f"expected_output_paths is only valid for file_writer tasks "
                f"(slug={self.slug!r}, task_type={self.task_type!r})"
            )
        if not self.output_filenames:
            raise AttributeError(
                f"output_filenames is empty for file_writer slug={self.slug!r}"
            )
        return [self.expected_dir / f for f in self.output_filenames]

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


def _parse_output_filenames(slug: str, data: dict[str, Any]) -> tuple[str, ...]:
    """Read file_writer output filenames from task.json.

    Accepts exactly one of (canonical names, plus their legacy ``_csv_`` aliases):
      - ``output_filename`` (str) — single-output schema.
      - ``output_filenames`` (non-empty list of str) — multi-output.

    Returns a tuple (length 1 for the single-output case). Filenames must be
    non-empty and unique — they become filenames under ``expected/`` and the
    student SLDB keys fetched at grade time, so a duplicate is ambiguous.
    """
    present_singular = [k for k in _SINGULAR_OUTPUT_KEYS if k in data]
    present_plural = [k for k in _PLURAL_OUTPUT_KEYS if k in data]
    present = present_singular + present_plural
    if len(present) > 1:
        raise ValueError(
            f"task.json for slug {slug!r} (file_writer) specifies multiple "
            f"output-filename keys ({present}); use exactly one of "
            f"'output_filename' / 'output_filenames'."
        )

    if present_plural:
        raw = data.pop(present_plural[0])
        if not isinstance(raw, list) or not raw:
            raise ValueError(
                f"task.json for slug {slug!r} (file_writer): "
                f"'{present_plural[0]}' must be a non-empty array."
            )
        filenames = [str(x) for x in raw]
    elif present_singular:
        filenames = [str(data.pop(present_singular[0]))]
    else:
        raise ValueError(
            f"task.json for slug {slug!r} (file_writer) must specify "
            f"'output_filename' (str) or 'output_filenames' (array)."
        )

    seen: set[str] = set()
    for f in filenames:
        if not f:
            raise ValueError(
                f"task.json for slug {slug!r} (file_writer) has an empty "
                f"output filename."
            )
        if f in seen:
            raise ValueError(
                f"Duplicate output filename {f!r} in slug {slug!r}; "
                f"output filenames must be unique (they're filenames in expected/)."
            )
        seen.add(f)
    return tuple(filenames)


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

    task_type = data.pop("task_type", TASK_TYPE_FILE_WRITER)
    task_type = TASK_TYPE_ALIASES.get(task_type, task_type)
    if task_type not in KNOWN_TASK_TYPES:
        raise ValueError(
            f"Unknown task_type {task_type!r} for slug {slug!r}. "
            f"Expected one of: {sorted(KNOWN_TASK_TYPES)}"
        )

    pipeline_path = data.pop("solution_pipeline_path")

    if task_type == TASK_TYPE_FILE_WRITER:
        output_match_mode = data.pop("output_match_mode", OUTPUT_MATCH_EXACT)
        if output_match_mode not in KNOWN_OUTPUT_MATCH_MODES:
            raise ValueError(
                f"Unknown output_match_mode {output_match_mode!r} for slug "
                f"{slug!r}. Expected one of: {sorted(KNOWN_OUTPUT_MATCH_MODES)}"
            )
        return TaskConfig(
            slug=slug,
            task_type=task_type,
            solution_pipeline_path=pipeline_path,
            output_filenames=_parse_output_filenames(slug, data),
            output_match_mode=output_match_mode,
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
