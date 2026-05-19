"""Deterministic orchestrator for a single exercise evaluation.

Flow:
  1. Strictly load the cached solution pipeline from
     `exercises/<task>/solution.json` (plus its sidecar and expected CSV).
     If anything is missing or the sidecar signature does not match the
     remote pipeline's timestamp, raise SolutionNotReadyError — the
     caller (grade or CLI) surfaces this as `needs_prep`. Refreshing the
     cache is /prep's job, not /grade's.
  2. Fetch the student pipeline definition from SnapLogic.
  3. Hard gate: pipeline names must match.
  4. Hard gate: CSV outputs must match (header + row multiset).
  5. If both gates pass, write an AI-context bundle to
     `.tmp/grades/<student>/<task>/ai_context.json` and emit
     "READY_FOR_AI_REVIEW" on stdout. The `grade` skill picks up from
     there and produces the final evaluation.json next to it.

This module never calls an LLM. Judgment lives in `.claude/skills/grade/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from .config import EXERCISES_DIR, TMP_DIR, load_settings
from .hard_gates import (
    GateResult,
    check_csv_outputs_match,
    check_pipeline_name_match,
)
from .pipeline_fetch import (
    PipelineLocation,
    SolutionNotReadyError,
    fetch_pipeline,
    fetch_pipeline_csv_output,
    flow_order_summary,
    load_cached_solution_pipeline,
)
from .snaplogic_client import SnapLogicClient
from .tasks import TaskConfig, list_tasks, load_task

READY_MARKER = "READY_FOR_AI_REVIEW"


def run_evaluation(
    task_slug: str,
    student_pipeline_path: str,
    *,
    student_name: str | None = None,
) -> int:
    try:
        task = load_task(task_slug)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    settings = load_settings()
    solution_loc = PipelineLocation.from_path(task.solution_pipeline_path)
    student_loc = PipelineLocation.from_path(student_pipeline_path)
    if student_name is None:
        student_name = student_loc.project

    student_task_dir = TMP_DIR / "grades" / student_name / task_slug
    student_subdir = student_task_dir / "student"

    print(f"[1/5] Solution pipeline: {solution_loc}")
    print(f"[1/5] Student pipeline:  {student_loc}")
    print(f"[1/5] Student artifacts: {student_task_dir}")

    with SnapLogicClient(settings) as client:
        print("[2/5] Resolving pipeline definitions...")
        solution = load_cached_solution_pipeline(
            client,
            solution_loc,
            task.solution_json_path,
            task.solution_cache_sidecar_path,
            task.expected_csv_path,
        )
        print(f"      solution cached -> {task.solution_json_path}")
        student = fetch_pipeline(client, student_loc, student_subdir)
        print(f"      student fetched -> {student.raw_json_path}")

        # Per-checkpoint version notes (Designer "Versions" dialog) —
        # the canonical place for bonus-question answers. Empty list
        # if the student never created a checkpoint.
        student_snode_id = client.find_pipeline_snode_id(
            student_loc.org, student_loc.project_space,
            student_loc.project, student_loc.name,
        )
        student_version_notes = client.get_pipeline_versions(student_snode_id)
        print(f"      student version notes: {len(student_version_notes)} checkpoint(s)")

        print("[3/5] Hard gate: pipeline name match...")
        name_gate = check_pipeline_name_match(solution.location.name, student.location.name)
        _print_gate(name_gate)
        if not name_gate.passed:
            return _write_fail_artifact(task, student_task_dir, name_gate)

        print("[4/5] Fetching student CSV output...")
        student_csv_path = student_subdir / task.output_csv_filename
        try:
            fetch_pipeline_csv_output(
                client, student_loc, task.output_csv_filename, student_csv_path
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            # Student didn't run their pipeline — SLDB has no output file.
            # Convert into a graceful csv_output_match failure instead of
            # crashing the whole plan loop.
            missing_gate = GateResult(
                name="csv_output_match",
                passed=False,
                detail=(
                    f"Student output file {task.output_csv_filename!r} not "
                    f"found in SLDB (HTTP 404) — the student likely did not "
                    f"run the pipeline, so no output exists to compare."
                ),
            )
            _print_gate(missing_gate)
            return _write_fail_artifact(task, student_task_dir, missing_gate, [name_gate])
        print(f"      expected CSV -> {task.expected_csv_path}")
        print(f"      student CSV  -> {student_csv_path}")

    print("[5/5] Hard gate: CSV output match...")
    csv_gate = check_csv_outputs_match(task.expected_csv_path, student_csv_path)
    _print_gate(csv_gate)
    if not csv_gate.passed:
        return _write_fail_artifact(task, student_task_dir, csv_gate, [name_gate])

    bundle_path = _write_ai_context(
        task=task,
        student_task_dir=student_task_dir,
        solution_definition=solution.definition,
        student_definition=student.definition,
        student_version_notes=student_version_notes,
        hard_gates=[name_gate, csv_gate],
    )
    print()
    print("=" * 70)
    print("HARD GATES PASSED — ready for AI review")
    print("=" * 70)
    print(f"AI context bundle: {bundle_path}")
    print(READY_MARKER)
    return 0


def _print_gate(g: GateResult) -> None:
    flag = "PASS" if g.passed else "FAIL"
    print(f"      [{flag}] {g.name}: {g.detail}")


def _write_fail_artifact(
    task: TaskConfig,
    student_task_dir: Path,
    failing_gate: GateResult,
    passed_gates: list[GateResult] | None = None,
) -> int:
    print()
    print("=" * 70)
    print(f"VERDICT: FAIL  (hard gate failed: {failing_gate.name})")
    print("=" * 70)
    print(failing_gate.detail)
    artifact = student_task_dir / "evaluation.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "verdict": "fail",
                "summary": f"Hard gate failed: {failing_gate.name}",
                "differences": [],
                "bonus_question_answer": None,
                "failing_gate": failing_gate.name,
                "failing_gate_detail": failing_gate.detail,
                "hard_gates": [
                    {"name": g.name, "passed": g.passed, "detail": g.detail}
                    for g in (passed_gates or []) + [failing_gate]
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Artifact written to {artifact}")
    return 1


def _write_ai_context(
    *,
    task: TaskConfig,
    student_task_dir: Path,
    solution_definition: dict,
    student_definition: dict,
    student_version_notes: list[dict],
    hard_gates: list[GateResult],
) -> Path:
    bundle_path = student_task_dir / "ai_context.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "task_slug": task.slug,
        "exercise_description": _read_text(task.description_path),
        "general_rules": _read_text(EXERCISES_DIR / "general_evaluation_rules.md"),
        "task_notes": _read_text(task.task_notes_path),
        "solution_flow": flow_order_summary(solution_definition),
        "student_flow": flow_order_summary(student_definition),
        "solution_definition": solution_definition,
        "student_definition": student_definition,
        "student_version_notes": student_version_notes,
        "hard_gates": [
            {"name": g.name, "passed": g.passed, "detail": g.detail}
            for g in hard_gates
        ],
    }
    bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return bundle_path


def _read_text(p: Path) -> str:
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evaluator",
        description=(
            "Deterministic evaluator: runs hard gates and emits an AI "
            "context bundle for the /grade skill to judge. Never calls an LLM."
        ),
    )
    parser.add_argument(
        "task",
        help=f"Task slug (folder under exercises/). Known: {list_tasks() or '<none registered yet>'}",
    )
    parser.add_argument(
        "--student",
        required=True,
        help="Student pipeline path: 'Org/ProjectSpace/Project/PipelineName'",
    )
    parser.add_argument(
        "--student-name",
        default=None,
        help="Override the student name used for output paths. Defaults to the "
             "project segment of --student.",
    )
    args = parser.parse_args(argv)
    try:
        return run_evaluation(
            args.task,
            args.student,
            student_name=args.student_name,
        )
    except SolutionNotReadyError as e:
        print(
            f"ERROR: Solution cache not ready ({e.status}): {e.reason}",
            file=sys.stderr,
        )
        print(
            "Run `python -m evaluator.prep sync` first to refresh the solution cache.",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
