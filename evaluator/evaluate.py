"""Deterministic orchestrator for a single exercise evaluation.

Flow (file_writer):
  1. Strictly load the cached solution pipeline from
     `exercises/<task>/solution.json` (plus its sidecar and expected output file(s)).
     If anything is missing or the sidecar signature does not match the
     remote pipeline's timestamp, raise SolutionNotReadyError — the
     caller (grade or CLI) surfaces this as `needs_prep`. Refreshing the
     cache is /prep's job, not /grade's.
  2. Fetch the student pipeline definition from SnapLogic.
  3. Hard gate: pipeline names must match (procedural — fail = 0 pts, no AI).
  4. Hard gate: the student's output file(s) exist in SLDB. If the 404
     fires (student never ran the pipeline), the exercise resolves to
     MISSING — there's nothing to grade, so it's excluded from totals
     rather than scored as 0/10. AI not invoked.
  5. Hard gate: output files must match (header + row multiset). If this
     gate fails, the verdict is still FAIL but the AI is invoked for
     partial credit — pipeline structure is judgeable even when output
     differs by a small amount.
  6. If both gates pass, write an AI-context bundle and emit READY_FOR_AI_REVIEW.

Flow (triggered_task):
  1. Strictly load the cached solution pipeline + every expected response.
  2. Fetch the student pipeline definition.
  3. Hard gate: pipeline names must match (procedural — fail = 0 pts, no AI).
  4. Hard gate: a Triggered Task named `<pipeline name> Task` must exist
     in the student's project. The convention is strict; failure here
     resolves to MISSING (excluded from totals), same as output_present
     404 — the student didn't submit a runnable deliverable.
  5. Invoke the student's Triggered Task once per scenario.
  6. Hard gate: every scenario's response must structurally match the
     cached expected response. If this gate fails, the verdict is still
     FAIL but the AI is invoked for partial credit.
  7. If all gates pass, write an AI-context bundle and emit READY_FOR_AI_REVIEW.

In both flows, output-mismatch FAILs (`output_match`,
`triggered_task_responses_match`) write an AI context bundle and exit 0
(READY_FOR_AI). The AI reads `hard_gates` in the bundle, sees the
failure, and emits `verdict: "fail"` with partial points. Procedural
FAILs (pipeline name wrong) write a complete `evaluation.json` directly
with 0 points and exit 1. "Deliverable not submitted" cases
(`output_present`, `triggered_task_exists`) write a MISSING
artifact and exit 4 — treated the same as "no matching pipeline" (not
graded, excluded from totals).

This module never calls an LLM. Judgment lives in `.claude/skills/grade/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import EXERCISES_DIR, TMP_DIR, load_settings
from .hard_gates import (
    GateResult,
    check_output_files_match_multi,
    check_pipeline_name_match,
    check_triggered_responses_match,
    check_triggered_task_exists,
)
from .pipeline_fetch import (
    PipelineLocation,
    SolutionNotReadyError,
    fetch_pipeline,
    fetch_pipeline_output_file,
    fetch_student_triggered_responses,
    flow_order_summary,
    load_cached_solution_pipeline,
    load_cached_solution_triggered_task,
)
from .snaplogic_client import SnapLogicClient
from .tasks import (
    OUTPUT_MATCH_COLUMNS_ONLY,
    TASK_TYPE_FILE_WRITER,
    TASK_TYPE_TRIGGERED_TASK,
    TaskConfig,
    list_tasks,
    load_task,
)

READY_MARKER = "READY_FOR_AI_REVIEW"

# Hard-gate failures that represent "output didn't match" rather than a
# procedural problem. For these, the AI judge is still invoked so it can
# award partial points for pipeline structure — the verdict stays FAIL
# (output is wrong) but points reflect the pipeline quality. Other
# hard-gate failures (pipeline name wrong, Triggered Task missing, no
# output file uploaded) are procedural — 0 points, no AI.
_OUTPUT_MISMATCH_GATES = frozenset({"output_match", "triggered_task_responses_match"})


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
    print(f"[1/5] Task type:         {task.task_type}")
    print(f"[1/5] Student artifacts: {student_task_dir}")

    if task.task_type == TASK_TYPE_FILE_WRITER:
        return _run_file_writer(
            task=task,
            solution_loc=solution_loc,
            student_loc=student_loc,
            settings=settings,
            student_task_dir=student_task_dir,
            student_subdir=student_subdir,
        )
    if task.task_type == TASK_TYPE_TRIGGERED_TASK:
        return _run_triggered_task(
            task=task,
            solution_loc=solution_loc,
            student_loc=student_loc,
            settings=settings,
            student_task_dir=student_task_dir,
            student_subdir=student_subdir,
        )
    print(
        f"ERROR: Unknown task_type {task.task_type!r} for slug {task_slug!r}.",
        file=sys.stderr,
    )
    return 2


def _run_file_writer(
    *,
    task: TaskConfig,
    solution_loc: PipelineLocation,
    student_loc: PipelineLocation,
    settings: Any,
    student_task_dir: Path,
    student_subdir: Path,
) -> int:
    with SnapLogicClient(settings) as client:
        print("[2/5] Resolving pipeline definitions...")
        solution = load_cached_solution_pipeline(
            client,
            solution_loc,
            task.solution_json_path,
            task.solution_cache_sidecar_path,
            task.expected_dir,
            task.output_filenames,
        )
        print(f"      solution cached -> {task.solution_json_path}")
        student = fetch_pipeline(client, student_loc, student_subdir)
        print(f"      student fetched -> {student.raw_json_path}")

        student_version_notes = _fetch_student_version_notes(client, student_loc)

        print("[3/5] Hard gate: pipeline name match...")
        name_gate = check_pipeline_name_match(solution.location.name, student.location.name)
        _print_gate(name_gate)
        if not name_gate.passed:
            # Procedural fail: 0 points, no AI.
            return _write_fail_artifact(task, student_task_dir, name_gate)

        n_outputs = len(task.output_filenames)
        print(f"[4/5] Fetching student output file(s) ({n_outputs} file(s))...")
        missing_files: list[str] = []
        for filename in task.output_filenames:
            dest = student_subdir / filename
            try:
                fetch_pipeline_output_file(client, student_loc, filename, dest)
                print(f"      student file -> {dest}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                missing_files.append(filename)
                print(f"      student file -> {filename}: NOT FOUND (HTTP 404)")

        if len(missing_files) == n_outputs:
            # No output files at all → student never ran the pipeline.
            missing_gate = GateResult(
                name="output_present",
                passed=False,
                detail=(
                    f"None of the {n_outputs} expected output file(s) "
                    f"({', '.join(task.output_filenames)}) were found in "
                    f"SLDB (HTTP 404) — the student likely did not run the "
                    f"pipeline, so no output exists to compare."
                ),
            )
            _print_gate(missing_gate)
            # No deliverable submitted → MISSING (excluded from totals).
            return _write_missing_artifact(
                task, student_task_dir, missing_gate, [name_gate]
            )

    columns_only = task.output_match_mode == OUTPUT_MATCH_COLUMNS_ONLY
    label = "columns only" if columns_only else "header + rows"
    print(f"[5/5] Hard gate: output match ({label})...")
    # Compare every registered output. A file that 404'd above points at a
    # non-existent student path, so the gate records it as missing (student
    # ran the pipeline but didn't produce that report) rather than excluding
    # the whole exercise from grading.
    gate_files = [
        (filename, task.expected_dir / filename, student_subdir / filename)
        for filename in task.output_filenames
    ]
    output_gate = check_output_files_match_multi(gate_files, columns_only=columns_only)
    _print_gate(output_gate)
    if not output_gate.passed:
        # Output mismatch — AI judges the pipeline for partial credit.
        return _handle_gate_failure(
            task=task,
            student_task_dir=student_task_dir,
            failing_gate=output_gate,
            passed_gates=[name_gate],
            solution_definition=solution.definition,
            student_definition=student.definition,
            student_version_notes=student_version_notes,
        )

    bundle_path = _write_ai_context(
        task=task,
        student_task_dir=student_task_dir,
        solution_definition=solution.definition,
        student_definition=student.definition,
        student_version_notes=student_version_notes,
        hard_gates=[name_gate, output_gate],
        extra={},
    )
    _print_ready(bundle_path)
    return 0


def _run_triggered_task(
    *,
    task: TaskConfig,
    solution_loc: PipelineLocation,
    student_loc: PipelineLocation,
    settings: Any,
    student_task_dir: Path,
    student_subdir: Path,
) -> int:
    assert task.triggered_task_name is not None
    student_responses_dir = student_subdir / "responses"
    expected_task_name = task.triggered_task_name

    with SnapLogicClient(settings) as client:
        print("[2/6] Resolving pipeline definitions...")
        solution = load_cached_solution_triggered_task(
            client,
            solution_loc,
            task.solution_json_path,
            task.solution_cache_sidecar_path,
            expected_dir=task.expected_dir,
            requests=task.requests,
        )
        print(f"      solution cached -> {task.solution_json_path}")
        student = fetch_pipeline(client, student_loc, student_subdir)
        print(f"      student fetched -> {student.raw_json_path}")

        student_version_notes = _fetch_student_version_notes(client, student_loc)

        print("[3/6] Hard gate: pipeline name match...")
        name_gate = check_pipeline_name_match(solution.location.name, student.location.name)
        _print_gate(name_gate)
        if not name_gate.passed:
            # Procedural fail: 0 points, no AI.
            return _write_fail_artifact(task, student_task_dir, name_gate)

        print(f"[4/6] Hard gate: Triggered Task {expected_task_name!r} exists...")
        try:
            student_task_entry = client.find_triggered_task_entry(
                student_loc.org, student_loc.project_space,
                student_loc.project, expected_task_name,
            )
        except LookupError:
            student_task_entry = None
        exists_gate = check_triggered_task_exists(expected_task_name, student_task_entry)
        _print_gate(exists_gate)
        if not exists_gate.passed:
            # No Triggered Task → the deliverable wasn't submitted.
            # MISSING (excluded from totals), same as output_present 404.
            return _write_missing_artifact(
                task, student_task_dir, exists_gate, [name_gate]
            )

        print(f"[5/6] Invoking student Triggered Task ({len(task.requests)} scenario(s))...")
        invocation_results = fetch_student_triggered_responses(
            client, student_loc, expected_task_name,
            task.requests, student_responses_dir,
        )
        for req in task.requests:
            path, status, err = invocation_results[req.name]
            label = err if err else f"HTTP {status}"
            print(f"      {req.name:<20} -> {label}  ({path.name})")

    print("[6/6] Hard gate: Triggered Task response match...")
    scenarios_for_gate: list[dict[str, Any]] = []
    for req in task.requests:
        path, status, err = invocation_results[req.name]
        scenarios_for_gate.append({
            "name": req.name,
            "expected_path": task.expected_response_path(req.name),
            "student_path": path,
            "student_http_status": status,
            "student_error": err,
        })
    responses_gate = check_triggered_responses_match(scenarios_for_gate)
    _print_gate(responses_gate)

    # Build per-scenario payload for the AI context — needed in both
    # the pass path and the output-mismatch-FAIL path so the AI can see
    # how each scenario's response differed.
    scenarios_payload = []
    for req in task.requests:
        path, status, err = invocation_results[req.name]
        scenarios_payload.append({
            "name": req.name,
            "params": dict(req.params),
            "expected": _read_json_or_text(task.expected_response_path(req.name)),
            "student": _read_json_or_text(path),
            "student_http_status": status,
            "student_error": err,
        })

    if not responses_gate.passed:
        # Output mismatch — AI judges the pipeline for partial credit.
        return _handle_gate_failure(
            task=task,
            student_task_dir=student_task_dir,
            failing_gate=responses_gate,
            passed_gates=[name_gate, exists_gate],
            solution_definition=solution.definition,
            student_definition=student.definition,
            student_version_notes=student_version_notes,
            extra={
                "triggered_task_name_expected": expected_task_name,
                "triggered_task_scenarios": scenarios_payload,
            },
        )

    bundle_path = _write_ai_context(
        task=task,
        student_task_dir=student_task_dir,
        solution_definition=solution.definition,
        student_definition=student.definition,
        student_version_notes=student_version_notes,
        hard_gates=[name_gate, exists_gate, responses_gate],
        extra={
            "triggered_task_name_expected": expected_task_name,
            "triggered_task_scenarios": scenarios_payload,
        },
    )
    _print_ready(bundle_path)
    return 0


def _fetch_student_version_notes(
    client: SnapLogicClient, student_loc: PipelineLocation,
) -> list[dict[str, Any]]:
    """Per-checkpoint Designer Versions notes — bonus-answer canonical home."""
    student_snode_id = client.find_pipeline_snode_id(
        student_loc.org, student_loc.project_space,
        student_loc.project, student_loc.name,
    )
    notes = client.get_pipeline_versions(student_snode_id)
    print(f"      student version notes: {len(notes)} checkpoint(s)")
    return notes


def _read_json_or_text(path: Path) -> Any:
    """Return parsed JSON, or the raw text if it isn't valid JSON."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"<{path.stat().st_size} bytes, non-utf8>"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _print_gate(g: GateResult) -> None:
    flag = "PASS" if g.passed else "FAIL"
    print(f"      [{flag}] {g.name}: {g.detail}")


def _print_ready(bundle_path: Path) -> None:
    print()
    print("=" * 70)
    print("HARD GATES PASSED — ready for AI review")
    print("=" * 70)
    print(f"AI context bundle: {bundle_path}")
    print(READY_MARKER)


def _write_missing_artifact(
    task: TaskConfig,
    student_task_dir: Path,
    failing_gate: GateResult,
    passed_gates: list[GateResult] | None = None,
) -> int:
    """Write a MISSING evaluation.json for 'deliverable not submitted' gates.

    Used for `output_present` (HTTP 404 from SLDB — student never
    ran their file_writer pipeline so no output file exists) and
    `triggered_task_exists` (no Triggered Task with the convention name
    in the student's project — they didn't create the deliverable for
    a triggered_task exercise). Treated the same as 'no matching
    pipeline': not graded, no point value (neither 0 nor 10), excluded
    from per-student totals. AI not invoked.

    Exit code 4 signals MISSING to `evaluator.grade.cmd_plan` so it can
    add the manifest entry with `status: "missing"`.
    """
    print()
    print("=" * 70)
    print(f"VERDICT: MISSING  (deliverable not submitted: {failing_gate.name})")
    print("=" * 70)
    print(failing_gate.detail)
    artifact = student_task_dir / "evaluation.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "verdict": "missing",
                "points": None,
                "summary": failing_gate.detail,
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
    return 4


def _write_fail_artifact(
    task: TaskConfig,
    student_task_dir: Path,
    failing_gate: GateResult,
    passed_gates: list[GateResult] | None = None,
) -> int:
    """Write a complete FAIL evaluation.json for *procedural* hard-gate fails.

    Used for pipeline-name mismatch — the student named the pipeline
    something other than the canonical name, so the deliverable IS
    there but doesn't follow convention. Not partial-credit material,
    so AI is not invoked and the score is fixed at 0 points. For
    output-mismatch fails (output_match,
    triggered_task_responses_match), use the AI-judged path
    (`_handle_gate_failure`). For "deliverable not submitted"
    (output_present, triggered_task_exists), use
    `_write_missing_artifact`.
    """
    print()
    print("=" * 70)
    print(f"VERDICT: FAIL  (hard gate failed: {failing_gate.name}) — 0 points, AI not invoked")
    print("=" * 70)
    print(failing_gate.detail)
    artifact = student_task_dir / "evaluation.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "verdict": "fail",
                "points": 0,
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


def _handle_gate_failure(
    *,
    task: TaskConfig,
    student_task_dir: Path,
    failing_gate: GateResult,
    passed_gates: list[GateResult],
    # Pipeline definitions are only needed for the AI-judged path. For
    # procedural fails (pipeline_name_match, etc.) the student definition
    # may not even be available — pass None and we go straight to the
    # zero-points artifact.
    solution_definition: dict | None = None,
    student_definition: dict | None = None,
    student_version_notes: list[dict] | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    """Route a hard-gate failure to either the AI judge or a 0-point artifact.

    Output-mismatch gates (`output_match`,
    `triggered_task_responses_match`) get the AI-judged path — the
    verdict stays FAIL but the AI awards partial points for pipeline
    structure. All other gates fall through to `_write_fail_artifact`
    (procedural FAIL, 0 points, no AI).
    """
    if (
        failing_gate.name in _OUTPUT_MISMATCH_GATES
        and solution_definition is not None
        and student_definition is not None
    ):
        bundle_path = _write_ai_context(
            task=task,
            student_task_dir=student_task_dir,
            solution_definition=solution_definition,
            student_definition=student_definition,
            student_version_notes=student_version_notes or [],
            hard_gates=passed_gates + [failing_gate],
            extra=extra or {},
        )
        print()
        print("=" * 70)
        print(f"OUTPUT MISMATCH ({failing_gate.name}) — routing to AI for partial credit")
        print("=" * 70)
        print(failing_gate.detail)
        print(f"AI context bundle: {bundle_path}")
        print(READY_MARKER)
        return 0
    return _write_fail_artifact(task, student_task_dir, failing_gate, passed_gates)


def _write_ai_context(
    *,
    task: TaskConfig,
    student_task_dir: Path,
    solution_definition: dict,
    student_definition: dict,
    student_version_notes: list[dict],
    hard_gates: list[GateResult],
    extra: dict[str, Any],
) -> Path:
    bundle_path = student_task_dir / "ai_context.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle: dict[str, Any] = {
        "task_slug": task.slug,
        "task_type": task.task_type,
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
    bundle.update(extra)
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
