"""Solution-side preparation for SnapLogic exercises.

Two subcommands:

    python -m evaluator.prep survey
        Walk exercises/, classify each folder, emit a plain summary plus
        a JSON block (delimited by SURVEY_JSON markers) for the /prep
        skill to parse. Read-only — never writes.

    python -m evaluator.prep sync [--slug X] [--output-csv FILENAME]
        Perform the writes. Creates exercises/<slug>/task.json when
        missing, then refreshes solution.json + sidecar + expected/<csv>
        via the existing cache logic. --slug limits the run to a single
        folder; --output-csv disambiguates a pipeline with zero or
        multiple binary-write snaps.

Design rule: the canonical pipeline name lives in the FIRST H1 HEADING
of `exercises/<slug>/description.md` (e.g. `# Task 01 – Generate CSV
Report`). Folder slugs can stay snake_case — they're filesystem-friendly
ids. When prep creates a task.json, it looks up the pipeline at
<org>/<solution_ps>/<solution_project>/<heading-from-description.md>.

Reconciliation rule: prep is the source-of-truth reconciler. Every
survey/sync re-reads the heading, looks up the pipeline live, fetches
the definition, and compares against task.json. If anything drifted —
pipeline renamed, writer filename renamed, snap structure changed,
cache stale, expected CSV missing — prep detects it and (on sync)
updates local files to match SnapLogic. /grade trusts the resulting
local files as ground truth.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any

from .config import EXERCISES_DIR, Settings, load_settings
from .pipeline_fetch import (
    PipelineLocation,
    SolutionNotReadyError,
    extract_binary_write_filenames,
    load_cached_solution_pipeline,
    load_or_refresh_solution_pipeline,
)
from .snaplogic_client import SnapLogicClient
from .tasks import (
    list_exercise_folders,
    load_task,
    read_pipeline_name_from_description,
)

STATUS_READY = "ready"
STATUS_NEEDS_TASK_JSON = "needs_task_json"
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
    output_csv_filename: str | None
    proposed_writer_filenames: list[str] | None
    reason: str


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
      4. ambiguous_writer — solution has 0 or >1 writers and we cannot pick one
      5. needs_task_json — no task.json yet (and writer is unambiguous)
      6. pipeline_renamed — heading-derived path differs from task.json's path
      7. writer_changed — single-writer filename differs from task.json's
      8. stale_solution — sidecar sig stale OR any cache file missing
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
            output_csv_filename=None,
            proposed_writer_filenames=None,
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
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            return FolderReport(
                slug=folder,
                status=STATUS_CONFIG_ERROR,
                task_json_exists=True,
                solution_pipeline_path=None,
                output_csv_filename=None,
                proposed_writer_filenames=None,
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
            output_csv_filename=task.output_csv_filename if task else None,
            proposed_writer_filenames=None,
            reason=(
                f"No pipeline named {pipeline_name!r} (read from "
                f"description.md heading) in "
                f"{settings.org_name}/{settings.project_space_name}/{settings.project_name}. "
                f"Create the pipeline with that exact name, or fix the heading."
            ),
        )

    definition = client.get_pipeline_definition(entry["snode_id"])
    writers = extract_binary_write_filenames(definition)

    # Pick the desired output filename based on live writers + task.json.
    # None means "ambiguous, caller must disambiguate".
    desired_output: str | None
    if len(writers) == 1:
        desired_output = writers[0]
    elif task is not None and task.output_csv_filename in writers:
        # Multiple writers, but task.json's choice is still valid — respect it.
        desired_output = task.output_csv_filename
    else:
        desired_output = None

    if desired_output is None:
        if task_exists:
            reason = (
                f"Solution pipeline has {len(writers)} binary-write snap(s) "
                f"({writers!r}); task.json's output_csv_filename="
                f"{task.output_csv_filename!r} is not among them. "
                f"Re-run with `sync --slug {folder} --output-csv FILENAME`."
            )
        else:
            reason = (
                f"Found {len(writers)} binary-write snap(s). Cannot auto-pick "
                f"output_csv_filename. Re-run with "
                f"`sync --slug {folder} --output-csv FILENAME`."
            )
        return FolderReport(
            slug=folder,
            status=STATUS_AMBIGUOUS_WRITER,
            task_json_exists=task_exists,
            solution_pipeline_path=proposed_path,
            output_csv_filename=task.output_csv_filename if task else None,
            proposed_writer_filenames=writers,
            reason=reason,
        )

    if not task_exists:
        return FolderReport(
            slug=folder,
            status=STATUS_NEEDS_TASK_JSON,
            task_json_exists=False,
            solution_pipeline_path=proposed_path,
            output_csv_filename=desired_output,
            proposed_writer_filenames=writers,
            reason=f"Will create task.json with output_csv_filename={desired_output!r}.",
        )

    if task.solution_pipeline_path != proposed_path:
        return FolderReport(
            slug=folder,
            status=STATUS_PIPELINE_RENAMED,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            output_csv_filename=desired_output,
            proposed_writer_filenames=writers,
            reason=(
                f"task.json's solution_pipeline_path "
                f"({task.solution_pipeline_path!r}) differs from the "
                f"heading-derived path ({proposed_path!r}). Sync will "
                f"rewrite task.json."
            ),
        )

    if task.output_csv_filename != desired_output:
        return FolderReport(
            slug=folder,
            status=STATUS_WRITER_CHANGED,
            task_json_exists=True,
            solution_pipeline_path=proposed_path,
            output_csv_filename=desired_output,
            proposed_writer_filenames=writers,
            reason=(
                f"Solution pipeline's binary-write filename "
                f"({desired_output!r}) differs from task.json's "
                f"output_csv_filename ({task.output_csv_filename!r}). "
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
            task.expected_csv_path,
        )
    except SolutionNotReadyError as e:
        return FolderReport(
            slug=folder,
            status=STATUS_STALE_SOLUTION,
            task_json_exists=True,
            solution_pipeline_path=task.solution_pipeline_path,
            output_csv_filename=task.output_csv_filename,
            proposed_writer_filenames=writers,
            reason=f"{e.status}: {e.reason}",
        )

    return FolderReport(
        slug=folder,
        status=STATUS_READY,
        task_json_exists=True,
        solution_pipeline_path=task.solution_pipeline_path,
        output_csv_filename=task.output_csv_filename,
        proposed_writer_filenames=writers,
        reason="Solution cache fresh and reconciled with SnapLogic; ready for /grade.",
    )


def cmd_survey() -> int:
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Copy .env.example to .env and fill in SnapLogic credentials.", file=sys.stderr)
        return 2

    folders = list_exercise_folders()
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


def _write_task_json(
    folder: str,
    solution_pipeline_path: str,
    output_csv_filename: str,
) -> None:
    path = EXERCISES_DIR / folder / "task.json"
    data = {
        "slug": folder,
        "solution_pipeline_path": solution_pipeline_path,
        "output_csv_filename": output_csv_filename,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        f"[{folder}] wrote task.json "
        f"(solution_pipeline_path={solution_pipeline_path!r}, "
        f"output_csv_filename={output_csv_filename!r})"
    )


def _reconcile_one(
    folder: str,
    client: SnapLogicClient,
    report: FolderReport,
) -> None:
    """Apply every auto-fixable drift in a single pass.

    Assumes the report's status is in AUTO_FIX_STATUSES. Rewrites
    task.json (if needed) using the report's reconciled values, then
    force-refreshes solution.json + sidecar + expected/<csv> against
    the live pipeline.
    """
    assert report.solution_pipeline_path is not None
    assert report.output_csv_filename is not None

    task_json_path = EXERCISES_DIR / folder / "task.json"
    need_write = True
    if task_json_path.exists():
        try:
            existing = load_task(folder)
            need_write = (
                existing.solution_pipeline_path != report.solution_pipeline_path
                or existing.output_csv_filename != report.output_csv_filename
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            need_write = True

    if need_write:
        _write_task_json(
            folder,
            report.solution_pipeline_path,
            report.output_csv_filename,
        )

    task = load_task(folder)
    loc = PipelineLocation.from_path(task.solution_pipeline_path)
    # Force-refresh: task.json may have just been rewritten, and the
    # remote signature may not have moved (e.g. cache-only repair).
    # An unconditional refetch guarantees disk matches the live pipeline.
    load_or_refresh_solution_pipeline(
        client,
        loc,
        task.solution_json_path,
        task.solution_cache_sidecar_path,
        expected_csv_path=task.expected_csv_path,
        output_csv_filename=task.output_csv_filename,
        force_refresh=True,
    )
    print(
        f"[{folder}] refreshed solution.json + expected/{task.output_csv_filename}"
    )

    _prune_expected_dir(folder, keep_filename=task.output_csv_filename)


def _prune_expected_dir(folder: str, *, keep_filename: str) -> None:
    """Delete every file in exercises/<folder>/expected/ except keep_filename.

    Prep owns this directory; the only file that should ever live here is
    the one named by task.output_csv_filename. Stale files accumulate when
    the solution pipeline's binary-write filename is renamed.
    """
    expected_dir = EXERCISES_DIR / folder / "expected"
    if not expected_dir.is_dir():
        return
    for entry in expected_dir.iterdir():
        if entry.is_file() and entry.name != keep_filename:
            entry.unlink()
            print(f"[{folder}] removed stale expected/{entry.name}")


def _sync_one(
    folder: str,
    client: SnapLogicClient,
    settings: Settings,
    output_csv_override: str | None,
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
    if report.status == STATUS_AMBIGUOUS_WRITER:
        if output_csv_override is None:
            print(
                f"[{folder}] AMBIGUOUS_WRITER — "
                f"writers: {report.proposed_writer_filenames}. "
                f"Pass --output-csv FILENAME to disambiguate.",
                file=sys.stderr,
            )
            return False
        assert report.solution_pipeline_path is not None
        # Build a synthetic report carrying the override, then reconcile.
        override_report = FolderReport(
            slug=folder,
            status=STATUS_AMBIGUOUS_WRITER,
            task_json_exists=report.task_json_exists,
            solution_pipeline_path=report.solution_pipeline_path,
            output_csv_filename=output_csv_override,
            proposed_writer_filenames=report.proposed_writer_filenames,
            reason=report.reason,
        )
        _reconcile_one(folder, client, override_report)
        return True
    if report.status in AUTO_FIX_STATUSES:
        _reconcile_one(folder, client, report)
        return True
    print(f"[{folder}] unknown status {report.status!r}", file=sys.stderr)
    return False


def cmd_sync(slug_filter: str | None, output_csv_override: str | None) -> int:
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
            # output_csv_override only applies when --slug pins a single folder;
            # blanket sync across folders must not reuse the same override.
            override = output_csv_override if slug_filter else None
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
            "refresh solution.json + expected/<csv> when SnapLogic pipelines change."
        ),
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    subparsers.add_parser(
        "survey",
        help="Classify every exercise folder. Read-only.",
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
        "--output-csv",
        default=None,
        help=(
            "Override output_csv_filename. Required together with --slug "
            "when the pipeline has zero or multiple binary-write snaps."
        ),
    )

    args = parser.parse_args(argv)
    if args.cmd == "survey":
        return cmd_survey()
    if args.cmd == "sync":
        return cmd_sync(args.slug, args.output_csv)
    parser.error(f"Unknown subcommand: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
