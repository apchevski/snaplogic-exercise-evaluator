"""Solution-side preparation for SnapLogic exercises.

Two subcommands:

    python -m evaluator.prep survey
        Walk exercises/, classify each folder, emit a plain summary plus
        a JSON block (delimited by SURVEY_JSON markers) for the /prep
        skill to parse. Read-only — never writes.

    python -m evaluator.prep sync [--slug X] [--output-file FILENAME]
        Perform the writes. Creates exercises/<slug>/task.json when
        possible (file_writer only — single-writer pipelines), then
        refreshes solution.json + sidecar + expected outputs via the
        existing cache logic. --slug limits the run to a single folder;
        --output-file disambiguates a file_writer pipeline with multiple
        binary-write snaps.

Two task types are supported. The /prep skill decides which type a new
folder should be by reading description.md + notes.md and writes the
initial task.json; sync then handles the API-side work.

  file_writer (default for back-compat):
    - Solution pipeline has binary-write snap(s) producing one or more files.
    - expected/ holds those output file(s).
  triggered_task:
    - Solution pipeline is exposed as a SnapLogic Triggered Task.
    - task.json lists scenarios (`requests`) to invoke.
    - expected/ holds one JSON file per scenario.

Design rule: the canonical pipeline name lives in the FIRST H1 HEADING
of `exercises/<slug>/description.md` (e.g. `# Task 01 – Generate CSV
Report`). Folder slugs can stay snake_case — they're filesystem-friendly
ids. When prep creates a task.json, it looks up the pipeline at
<org>/<solution_ps>/<solution_project>/<heading-from-description.md>.

Reconciliation rule: prep is the source-of-truth reconciler. Every
survey/sync re-reads the heading, looks up the pipeline live, fetches
the definition, and compares against task.json. If anything drifted —
pipeline renamed, writer filename renamed, snap structure changed,
cache stale, expected outputs missing — prep detects it and (on sync)
updates local files to match SnapLogic. /grade trusts the resulting
local files as ground truth.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import EXERCISES_DIR, Settings, load_settings
from .name_match import pipeline_paths_match
from .pipeline_fetch import (
    PipelineLocation,
    SolutionNotReadyError,
    _extract_remote_signature,
    extract_binary_write_filenames,
    load_cached_solution_pipeline,
    load_or_refresh_solution_pipeline,
    load_or_refresh_solution_triggered_task,
)
from .snaplogic_client import SnapLogicClient
from .tasks import (
    OUTPUT_MATCH_EXACT,
    TASK_TYPE_FILE_WRITER,
    TASK_TYPE_TRIGGERED_TASK,
    list_exercise_folders,
    load_task,
    read_pipeline_name_from_description,
)

STATUS_READY = "ready"
STATUS_NEEDS_TASK_JSON = "needs_task_json"
STATUS_NEEDS_TASK_JSON_TRIGGERED = "needs_task_json_triggered"
STATUS_STALE_SOLUTION = "stale_solution"
STATUS_PIPELINE_NOT_FOUND = "pipeline_not_found"
STATUS_PIPELINE_RENAMED = "pipeline_renamed"
STATUS_WRITER_CHANGED = "writer_changed"
STATUS_AMBIGUOUS_WRITER = "ambiguous_writer"
STATUS_CONFIG_ERROR = "config_error"
STATUS_MISSING_DESCRIPTION = "missing_description"

AUTO_FIX_STATUSES = frozenset({
    STATUS_NEEDS_TASK_JSON,
    STATUS_STALE_SOLUTION,
    STATUS_PIPELINE_RENAMED,
    STATUS_WRITER_CHANGED,
})

SURVEY_JSON_BEGIN = "---SURVEY_JSON_BEGIN---"
SURVEY_JSON_END = "---SURVEY_JSON_END---"


@dataclass
class FolderReport:
    slug: str
    status: str
    task_json_exists: bool
    solution_pipeline_path: str | None
    reason: str
    # file_writer-specific fields. output_filenames is the full registered
    # list (one or more); output_filename mirrors it only when there's
    # exactly one, so single-output surveys read unchanged.
    output_filename: str | None = None
    output_filenames: list[str] | None = None
    proposed_writer_filenames: list[str] | None = None
    # triggered_task-specific fields
    task_type: str | None = None
    triggered_task_name: str | None = None
    request_names: list[str] | None = None
    # The .json files prep expects to find in expected/, for the skill's
    # benefit when it's deciding whether sync did the right thing.
    expected_response_filenames: list[str] | None = None


def _proposed_path(settings: Settings, pipeline_name: str) -> str:
    return (
        f"{settings.org_name}/{settings.project_space_name}/"
        f"{settings.project_name}/{pipeline_name}"
    )


def _classify_folder(
    folder: str,
    client: SnapLogicClient,
    settings: Settings,
) -> FolderReport:
    """Compare live SnapLogic state against local files for one folder.

    Reconciliation order (first match wins):
      1. missing_description — no description.md heading
      2. config_error — task.json present but unreadable
      3. pipeline_not_found — heading-named pipeline doesn't exist in SnapLogic
      4. (file_writer only) ambiguous_writer — solution has >1 writers and we cannot pick
      5. needs_task_json — no task.json yet AND pipeline has 1 writer (file_writer fast path)
      6. needs_task_json_triggered — no task.json yet AND pipeline has 0 writers
         (skill must decide task_type + write task.json from description.md/notes.md)
      7. (file_writer) pipeline_renamed, writer_changed, stale_solution
      8. (triggered_task) pipeline_renamed, stale_solution
      9. ready
    """
    folder_dir = EXERCISES_DIR / folder
    task_json_path = folder_dir / "task.json"
    task_exists = task_json_path.exists()

    pipeline_name = read_pipeline_name_from_description(folder)
    if pipeline_name is None:
        return FolderReport(
            slug=folder,
            status=STATUS_MISSING_DESCRIPTION,
            task_json_exists=task_exists,
            solution_pipeline_path=None,
            reason=(
                f"exercises/{folder}/description.md is missing or has no "
                f"`# Heading` on the first line. The H1 heading is the "
                f"canonical pipeline name; add one (e.g. `# Task 01 – "
                f"Generate CSV Report`) and re-run."
            ),
        )

    proposed_path = _proposed_path(settings, pipeline_name)

    task: Any = None
    if task_exists:
        try:
            task = load_task(folder)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
            return FolderReport(
                slug=folder,
                status=STATUS_CONFIG_ERROR,
                task_json_exists=True,
                solution_pipeline_path=None,
                reason=f"Cannot load task.json: {e}",
            )

    try:
        entry = client.find_pipeline_asset_entry(
            settings.org_name,
            settings.project_space_name,
            settings.project_name,
            pipeline_name,
        )
    except LookupError:
        return FolderReport(
            slug=folder,
            status=STATUS_PIPELINE_NOT_FOUND,
            task_json_exists=task_exists,
            solution_pipeline_path=proposed_path,
            output_filenames=(
                list(task.output_filenames)
                if task and task.task_type == TASK_TYPE_FILE_WRITER
                else None
            ),
            task_type=task.task_type if task else None,
            triggered_task_name=task.triggered_task_name if task else None,
            reason=(
                f"No pipeline named {pipeline_name!r} (read from "
                f"description.md heading) in "
                f"{settings.org_name}/{settings.project_space_name}/{settings.project_name}. "
                f"Create the pipeline with that exact name, or fix the heading."
            ),
        )

    # Branch on whether task.json already exists.
    if task is None:
        return _classify_no_task_json(
            folder, client, entry, proposed_path
        )

    if task.task_type == TASK_TYPE_TRIGGERED_TASK:
        return _classify_triggered_task(folder, client, task, entry, proposed_path)
    return _classify_file_writer(folder, client, task, entry, proposed_path)


def _classify_no_task_json(
    folder: str,
    client: SnapLogicClient,
    pipeline_entry: dict[str, Any],
    proposed_path: str,
) -> FolderReport:
    """task.json missing — decide whether to auto-create or defer to the skill."""
    definition = client.get_pipeline_definition(pipeline_entry["snode_id"])
    writers = extract_binary_write_filenames(definition)

    if len(writers) == 1:
        return FolderReport(
            slug=folder,
            status=STATUS_NEEDS_TASK_JSON,
            task_json_exists=False,
            solution_pipeline_path=proposed_path,
            output_filename=writers[0],
            output_filenames=[writers[0]],
            proposed_writer_filenames=writers,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=(
                f"Will create task.json (file_writer) with "
                f"output_filename={writers[0]!r}."
            ),
        )

    if len(writers) == 0:
        return FolderReport(
            slug=folder,
            status=STATUS_NEEDS_TASK_JSON_TRIGGERED,
            task_json_exists=False,
            solution_pipeline_path=proposed_path,
            proposed_writer_filenames=writers,
            reason=(
                "No task.json and the solution pipeline has 0 binary-write "
                "snaps — likely a triggered-task exercise. The /prep skill "
                "must read description.md + notes.md to decide the task "
                "type and write task.json (triggered_task_name + requests). "
                "Re-run sync after task.json exists."
            ),
        )

    return FolderReport(
        slug=folder,
        status=STATUS_AMBIGUOUS_WRITER,
        task_json_exists=False,
        solution_pipeline_path=proposed_path,
        proposed_writer_filenames=writers,
        reason=(
            f"Found {len(writers)} binary-write snap(s); the skill must decide. "
            f"If one is the canonical output, re-run with "
            f"`sync --slug {folder} --output-file FILENAME`. If all are required "
            f"deliverables, hand-write task.json with "
            f"`output_filenames: [...]` then re-run `sync --slug {folder}`."
        ),
    )


def _classify_file_writer(
    folder: str,
    client: SnapLogicClient,
    task: Any,
    pipeline_entry: dict[str, Any],
    proposed_path: str,
) -> FolderReport:
    definition = client.get_pipeline_definition(pipeline_entry["snode_id"])
    writers = extract_binary_write_filenames(definition)
    registered = list(task.output_filenames)

    # Resolve the output set task.json should hold.
    #   - Single-output back-compat: a pipeline with exactly one writer
    #     auto-follows that writer's filename (so a rename in Designer is
    #     reconciled without the user touching task.json).
    #   - Otherwise every registered filename must still be a live writer.
    #     A multi-output task.json is hand-authored, so we never guess a
    #     mapping — if a registered file is no longer written, the skill
    #     must re-decide (ambiguous_writer).
    if len(registered) == 1 and len(writers) == 1:
        desired_outputs: list[str] | None = [writers[0]]
    elif registered and all(f in writers for f in registered):
        desired_outputs = registered
    else:
        desired_outputs = None

    if desired_outputs is None:
        reason = (
            f"Solution pipeline has {len(writers)} binary-write snap(s) "
            f"({writers!r}); task.json's output_filenames={registered!r} "
            f"are not all among them. Re-run with "
            f"`sync --slug {folder} --output-file FILENAME` (single canonical "
            f"output) or hand-fix task.json's output_filenames then "
            f"`sync --slug {folder}`."
        )
        return FolderReport(
            slug=folder,
            status=STATUS_AMBIGUOUS_WRITER,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            output_filename=registered[0] if len(registered) == 1 else None,
            output_filenames=registered,
            proposed_writer_filenames=writers,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=reason,
        )

    single = desired_outputs[0] if len(desired_outputs) == 1 else None

    if not pipeline_paths_match(task.solution_pipeline_path, proposed_path):
        return FolderReport(
            slug=folder,
            status=STATUS_PIPELINE_RENAMED,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            output_filename=single,
            output_filenames=desired_outputs,
            proposed_writer_filenames=writers,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=(
                f"task.json's solution_pipeline_path "
                f"({task.solution_pipeline_path!r}) differs from the "
                f"heading-derived path ({proposed_path!r}). Sync will "
                f"rewrite the path."
            ),
        )

    if registered != desired_outputs:
        # Only reachable in the single-writer case (multi sets desired=registered).
        return FolderReport(
            slug=folder,
            status=STATUS_WRITER_CHANGED,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            output_filename=single,
            output_filenames=desired_outputs,
            proposed_writer_filenames=writers,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=(
                f"Solution pipeline's binary-write filename "
                f"({desired_outputs!r}) differs from task.json's "
                f"output_filenames ({registered!r}). "
                f"Sync will rewrite task.json + expected/."
            ),
        )

    loc = PipelineLocation.from_path(task.solution_pipeline_path)
    try:
        load_cached_solution_pipeline(
            client,
            loc,
            task.solution_json_path,
            task.solution_cache_sidecar_path,
            task.expected_dir,
            task.output_filenames,
        )
    except SolutionNotReadyError as e:
        return FolderReport(
            slug=folder,
            status=STATUS_STALE_SOLUTION,
            task_json_exists=True,
            solution_pipeline_path=task.solution_pipeline_path,
            output_filename=single,
            output_filenames=registered,
            proposed_writer_filenames=writers,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=f"{e.status}: {e.reason}",
        )

    return FolderReport(
        slug=folder,
        status=STATUS_READY,
        task_json_exists=True,
        solution_pipeline_path=task.solution_pipeline_path,
        output_filename=single,
        output_filenames=registered,
        proposed_writer_filenames=writers,
        task_type=TASK_TYPE_FILE_WRITER,
        reason="Solution cache fresh and reconciled with SnapLogic; ready for /grade.",
    )


def _classify_triggered_task(
    folder: str,
    client: SnapLogicClient,
    task: Any,
    pipeline_entry: dict[str, Any],
    proposed_path: str,
) -> FolderReport:
    request_names = [r.name for r in task.requests]
    expected_filenames = list(task.expected_response_filenames)

    if not pipeline_paths_match(task.solution_pipeline_path, proposed_path):
        return FolderReport(
            slug=folder,
            status=STATUS_PIPELINE_RENAMED,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            task_type=TASK_TYPE_TRIGGERED_TASK,
            triggered_task_name=task.triggered_task_name,
            request_names=request_names,
            expected_response_filenames=expected_filenames,
            reason=(
                f"task.json's solution_pipeline_path "
                f"({task.solution_pipeline_path!r}) differs from the "
                f"heading-derived path ({proposed_path!r}). Sync will "
                f"rewrite task.json + refresh expected/ JSON responses."
            ),
        )

    # Freshness check: signature + every expected/<name>.json must exist.
    missing: list[str] = []
    if not task.solution_json_path.exists():
        missing.append("solution.json")
    if not task.solution_cache_sidecar_path.exists():
        missing.append("solution.cache.json")
    for fname in expected_filenames:
        if not (task.expected_dir / fname).exists():
            missing.append(f"expected/{fname}")

    if missing:
        return FolderReport(
            slug=folder,
            status=STATUS_STALE_SOLUTION,
            task_json_exists=True,
            solution_pipeline_path=task.solution_pipeline_path,
            task_type=TASK_TYPE_TRIGGERED_TASK,
            triggered_task_name=task.triggered_task_name,
            request_names=request_names,
            expected_response_filenames=expected_filenames,
            reason=f"Missing cache files: {missing!r}. Sync will refresh.",
        )

    remote_sig = _extract_remote_signature(pipeline_entry)
    try:
        sidecar = json.loads(task.solution_cache_sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return FolderReport(
            slug=folder,
            status=STATUS_STALE_SOLUTION,
            task_json_exists=True,
            solution_pipeline_path=task.solution_pipeline_path,
            task_type=TASK_TYPE_TRIGGERED_TASK,
            triggered_task_name=task.triggered_task_name,
            request_names=request_names,
            expected_response_filenames=expected_filenames,
            reason=f"Unreadable sidecar: {e}. Sync will refresh.",
        )

    if remote_sig is not None:
        sidecar_sig = (sidecar.get("signature_kind"), sidecar.get("signature"))
        if sidecar_sig != remote_sig:
            return FolderReport(
                slug=folder,
                status=STATUS_STALE_SOLUTION,
                task_json_exists=True,
                solution_pipeline_path=task.solution_pipeline_path,
                task_type=TASK_TYPE_TRIGGERED_TASK,
                triggered_task_name=task.triggered_task_name,
                request_names=request_names,
                expected_response_filenames=expected_filenames,
                reason=(
                    f"Pipeline signature drifted: sidecar={sidecar_sig}, "
                    f"remote={remote_sig}. Sync will refresh."
                ),
            )

    return FolderReport(
        slug=folder,
        status=STATUS_READY,
        task_json_exists=True,
        solution_pipeline_path=task.solution_pipeline_path,
        task_type=TASK_TYPE_TRIGGERED_TASK,
        triggered_task_name=task.triggered_task_name,
        request_names=request_names,
        expected_response_filenames=expected_filenames,
        reason=(
            f"Solution cache fresh and reconciled with SnapLogic; "
            f"{len(request_names)} scenario(s) in expected/."
        ),
    )


def cmd_survey(slug_filter: str | None = None) -> int:
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Copy .env.example to .env and fill in SnapLogic credentials.", file=sys.stderr)
        return 2

    folders = list_exercise_folders()
    if slug_filter is not None:
        if slug_filter not in folders:
            print(
                f"ERROR: No exercise folder named {slug_filter!r}. Known: {folders}",
                file=sys.stderr,
            )
            return 2
        folders = [slug_filter]

    print(
        f"Solution project: {settings.org_name}/{settings.project_space_name}/{settings.project_name}"
    )
    print(f"Exercise folders: {len(folders)}")
    print("-" * 60)

    reports: list[FolderReport] = []
    with SnapLogicClient(settings) as client:
        for folder in folders:
            report = _classify_folder(folder, client, settings)
            reports.append(report)
            print(f"[{folder}] {report.status} — {report.reason}")

    print()
    print(SURVEY_JSON_BEGIN)
    print(json.dumps([asdict(r) for r in reports], indent=2))
    print(SURVEY_JSON_END)
    return 0


def _write_file_writer_task_json(
    folder: str,
    solution_pipeline_path: str,
    output_filename: str,
) -> None:
    path = EXERCISES_DIR / folder / "task.json"
    data = {
        "slug": folder,
        "task_type": TASK_TYPE_FILE_WRITER,
        "solution_pipeline_path": solution_pipeline_path,
        "output_filename": output_filename,
    }
    # The writer regenerates task.json from scratch, but output_match_mode is
    # a hand-set field the writer doesn't manage (currently only "columns_only"
    # for non-deterministic-output exercises like task_04_born_on_friday).
    # Carry it through a regeneration so reconciling a rename doesn't silently
    # revert the output gate to the "exact" default.
    preserved_mode = _existing_output_match_mode(path)
    if preserved_mode is not None:
        data["output_match_mode"] = preserved_mode
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    extra = (
        f", output_match_mode={preserved_mode!r}" if preserved_mode is not None else ""
    )
    print(
        f"[{folder}] wrote task.json "
        f"(task_type=file_writer, "
        f"solution_pipeline_path={solution_pipeline_path!r}, "
        f"output_filename={output_filename!r}{extra})"
    )


def _existing_output_match_mode(task_json_path: Path) -> str | None:
    """Return a non-default output_match_mode already in task.json, if any.

    Lets :func:`_write_file_writer_task_json` carry a hand-authored
    output_match_mode (only "columns_only" today) through a regeneration
    instead of dropping it back to the "exact" default. Returns None when the
    file is absent (fresh create), unreadable, or already uses the default.
    """
    try:
        data = json.loads(task_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = data.get("output_match_mode")
    if mode and mode != OUTPUT_MATCH_EXACT:
        return mode
    return None


def _reconcile_file_writer(
    folder: str,
    client: SnapLogicClient,
    report: FolderReport,
) -> None:
    """Apply every file_writer drift in one pass.

    Single-output exercises are auto-managed: task.json is (re)generated from
    the heading-derived path + the live writer. Multi-output exercises are
    hand-authored (like triggered_task) — their filename list is preserved
    and never regenerated; we only fix a drifted solution_pipeline_path and
    refresh the cache + every expected output file.
    """
    assert report.solution_pipeline_path is not None
    assert report.output_filenames is not None
    desired = report.output_filenames
    is_multi = len(desired) > 1

    task_json_path = EXERCISES_DIR / folder / "task.json"

    if is_multi:
        # Preserve the hand-authored output_filenames list; only correct a
        # pipeline rename (path drift) before refreshing.
        if task_json_path.exists():
            try:
                existing = load_task(folder)
                if not pipeline_paths_match(
                    existing.solution_pipeline_path, report.solution_pipeline_path
                ):
                    _rewrite_solution_pipeline_path(folder, report.solution_pipeline_path)
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                pass
    else:
        need_write = True
        if task_json_path.exists():
            try:
                existing = load_task(folder)
                need_write = (
                    existing.task_type != TASK_TYPE_FILE_WRITER
                    or not pipeline_paths_match(
                        existing.solution_pipeline_path, report.solution_pipeline_path
                    )
                    or list(existing.output_filenames) != desired
                )
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                need_write = True
        if need_write:
            _write_file_writer_task_json(folder, report.solution_pipeline_path, desired[0])

    task = load_task(folder)
    loc = PipelineLocation.from_path(task.solution_pipeline_path)
    load_or_refresh_solution_pipeline(
        client,
        loc,
        task.solution_json_path,
        task.solution_cache_sidecar_path,
        expected_dir=task.expected_dir,
        output_filenames=task.output_filenames,
        force_refresh=True,
    )
    print(
        f"[{folder}] refreshed solution.json + "
        f"expected/ ({', '.join(task.output_filenames)})"
    )
    _prune_expected_dir(folder, keep_filenames=set(task.output_filenames))


def _rewrite_solution_pipeline_path(folder: str, new_path: str) -> None:
    """Update only solution_pipeline_path in an existing task.json.

    Used to auto-fix a pipeline rename for triggered_task exercises.
    Unlike file_writer (whose task.json is fully regenerated by
    _write_file_writer_task_json), a triggered_task task.json is hand-authored —
    triggered_task_name + requests/params must be preserved field-for-
    field — so we load the raw dict, replace the one key, and rewrite.
    """
    path = EXERCISES_DIR / folder / "task.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    old_path = data.get("solution_pipeline_path")
    data["solution_pipeline_path"] = new_path
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        f"[{folder}] rewrote task.json solution_pipeline_path "
        f"{old_path!r} -> {new_path!r}"
    )


def _reconcile_triggered_task(
    folder: str,
    client: SnapLogicClient,
    report: FolderReport,
) -> None:
    """Apply every triggered_task drift in one pass.

    task.json must already exist for triggered_task — the skill writes
    it from description.md + notes.md before invoking sync. This
    function never creates task.json itself.
    """
    assert report.solution_pipeline_path is not None
    task = load_task(folder)
    assert task.task_type == TASK_TYPE_TRIGGERED_TASK
    assert task.triggered_task_name is not None

    # Pipeline-rename drift: report.solution_pipeline_path is the canonical
    # heading-derived path. Rewrite task.json's stale path BEFORE refreshing
    # so the cache + expected/ responses come from the correct location.
    # Guarded by pipeline_paths_match so stale_solution (same path) is a no-op.
    if not pipeline_paths_match(
        task.solution_pipeline_path, report.solution_pipeline_path
    ):
        _rewrite_solution_pipeline_path(folder, report.solution_pipeline_path)
        task = load_task(folder)

    loc = PipelineLocation.from_path(task.solution_pipeline_path)
    load_or_refresh_solution_triggered_task(
        client,
        loc,
        task.solution_json_path,
        task.solution_cache_sidecar_path,
        expected_dir=task.expected_dir,
        triggered_task_name=task.triggered_task_name,
        requests=task.requests,
        force_refresh=True,
    )
    print(
        f"[{folder}] refreshed solution.json + "
        f"{len(task.requests)} response(s) in expected/"
    )
    _prune_expected_dir(folder, keep_filenames=set(task.expected_response_filenames))


def _prune_expected_dir(folder: str, *, keep_filenames: set[str]) -> None:
    """Delete every file in exercises/<folder>/expected/ not in keep_filenames.

    Prep owns this directory; only files registered in task.json should
    live here. Stale files accumulate when a writer is renamed or a
    triggered-task scenario is removed.
    """
    expected_dir = EXERCISES_DIR / folder / "expected"
    if not expected_dir.is_dir():
        return
    for entry in expected_dir.iterdir():
        if entry.is_file() and entry.name not in keep_filenames:
            entry.unlink()
            print(f"[{folder}] removed stale expected/{entry.name}")


def _sync_one(
    folder: str,
    client: SnapLogicClient,
    settings: Settings,
    output_file_override: str | None,
) -> bool:
    """Sync a single folder. Returns True if at least one write happened."""
    report = _classify_folder(folder, client, settings)
    if report.status == STATUS_READY:
        print(f"[{folder}] already ready — nothing to do.")
        return False
    if report.status == STATUS_CONFIG_ERROR:
        print(f"[{folder}] CONFIG_ERROR — {report.reason}", file=sys.stderr)
        return False
    if report.status == STATUS_PIPELINE_NOT_FOUND:
        print(f"[{folder}] PIPELINE_NOT_FOUND — {report.reason}", file=sys.stderr)
        return False
    if report.status == STATUS_MISSING_DESCRIPTION:
        print(f"[{folder}] MISSING_DESCRIPTION — {report.reason}", file=sys.stderr)
        return False
    if report.status == STATUS_NEEDS_TASK_JSON_TRIGGERED:
        # The Python script can't derive scenarios from prose; the skill
        # must write task.json (triggered_task_name + requests) first.
        print(
            f"[{folder}] NEEDS_TASK_JSON_TRIGGERED — {report.reason}",
            file=sys.stderr,
        )
        return False
    if report.status == STATUS_AMBIGUOUS_WRITER:
        if output_file_override is None:
            print(
                f"[{folder}] AMBIGUOUS_WRITER — "
                f"writers: {report.proposed_writer_filenames}. "
                f"Pass --output-file FILENAME to disambiguate.",
                file=sys.stderr,
            )
            return False
        assert report.solution_pipeline_path is not None
        override_report = FolderReport(
            slug=folder,
            status=STATUS_AMBIGUOUS_WRITER,
            task_json_exists=report.task_json_exists,
            solution_pipeline_path=report.solution_pipeline_path,
            output_filename=output_file_override,
            output_filenames=[output_file_override],
            proposed_writer_filenames=report.proposed_writer_filenames,
            task_type=TASK_TYPE_FILE_WRITER,
            reason=report.reason,
        )
        _reconcile_file_writer(folder, client, override_report)
        return True
    if report.status in AUTO_FIX_STATUSES:
        if report.task_type == TASK_TYPE_TRIGGERED_TASK:
            _reconcile_triggered_task(folder, client, report)
        else:
            _reconcile_file_writer(folder, client, report)
        return True
    print(f"[{folder}] unknown status {report.status!r}", file=sys.stderr)
    return False


def cmd_sync(slug_filter: str | None, output_file_override: str | None) -> int:
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    folders = list_exercise_folders()
    if slug_filter is not None:
        if slug_filter not in folders:
            print(
                f"ERROR: No exercise folder named {slug_filter!r}. Known: {folders}",
                file=sys.stderr,
            )
            return 2
        folders = [slug_filter]

    write_count = 0
    with SnapLogicClient(settings) as client:
        for folder in folders:
            # output_file_override only applies when --slug pins a single folder;
            # blanket sync across folders must not reuse the same override.
            override = output_file_override if slug_filter else None
            if _sync_one(folder, client, settings, override):
                write_count += 1

    print()
    print(f"Sync complete. {write_count} folder(s) updated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evaluator.prep",
        description=(
            "Prepare exercise folders: create task.json for new folders, "
            "refresh solution.json + expected/ when SnapLogic pipelines change."
        ),
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    p_survey = subparsers.add_parser(
        "survey",
        help="Classify every exercise folder. Read-only.",
    )
    p_survey.add_argument(
        "--slug",
        default=None,
        help="Limit survey to a single folder (folder name under exercises/).",
    )
    p_sync = subparsers.add_parser(
        "sync",
        help="Create task.json and/or refresh solution cache.",
    )
    p_sync.add_argument(
        "--slug",
        default=None,
        help="Limit sync to a single folder (folder name under exercises/).",
    )
    p_sync.add_argument(
        "--output-file",
        "--output-csv",  # back-compat alias for the original flag name
        dest="output_file",
        default=None,
        help=(
            "Override output_filename for file_writer tasks. Required "
            "with --slug when a file_writer pipeline has multiple binary-"
            "write snaps and one is the canonical output. Ignored for "
            "triggered_task. (`--output-csv` is a deprecated alias.)"
        ),
    )

    args = parser.parse_args(argv)
    if args.cmd == "survey":
        return cmd_survey(args.slug)
    if args.cmd == "sync":
        return cmd_sync(args.slug, args.output_file)
    parser.error(f"Unknown subcommand: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
