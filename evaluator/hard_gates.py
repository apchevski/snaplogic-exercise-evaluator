"""Deterministic pre-AI checks that can short-circuit an evaluation.

These are 'hard rules' — if any fail, the exercise is automatically
failed and we do NOT spend tokens asking the AI.

Current hard gates (see exercises/general_evaluation_rules.md):
  1. Student pipeline name must exactly match solution pipeline name.
  2a. (csv_writer) Student CSV output must exactly match solution CSV
      output (rows, not byte order — we sort+compare so trivial encoding
      differences don't false-fail).
  2b. (triggered_task) A Triggered Task with the convention name
      (`<pipeline name> Task`) must exist in the student's project, AND
      every scenario's JSON response must match the cached expected
      response structurally.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .name_match import names_match


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


def check_pipeline_name_match(solution_name: str, student_name: str) -> GateResult:
    """Compare pipeline names. Exact match except dash glyphs are interchangeable.

    See `evaluator.name_match`: en-dash / em-dash / hyphen-minus all count
    as the same character. Everything else (case, spacing, punctuation)
    must match exactly.
    """
    passed = names_match(solution_name, student_name)
    if passed:
        if solution_name == student_name:
            detail = f"Pipeline name matches: {solution_name!r}"
        else:
            detail = (
                f"Pipeline name matches (dash glyph differs): "
                f"solution={solution_name!r}, student={student_name!r}"
            )
    else:
        detail = (
            f"Pipeline name mismatch — solution={solution_name!r}, "
            f"student={student_name!r}"
        )
    return GateResult(name="pipeline_name_match", passed=passed, detail=detail)


def check_csv_outputs_match(
    expected_csv: Path,
    actual_csv: Path,
) -> GateResult:
    """Compare two CSVs row-by-row, header-aware.

    Both files must have identical header sets and identical row
    multisets. Row order does NOT matter at this gate — pipeline ordering
    (sort/filter placement) is handled by the AI evaluator on the
    pipeline structure, not by re-checking output order here.
    """
    if not expected_csv.exists():
        return GateResult(
            name="csv_output_match",
            passed=False,
            detail=f"Expected CSV not found at {expected_csv}",
        )
    if not actual_csv.exists():
        return GateResult(
            name="csv_output_match",
            passed=False,
            detail=f"Student CSV not found at {actual_csv}",
        )

    exp_header, exp_rows = _read_csv(expected_csv)
    act_header, act_rows = _read_csv(actual_csv)

    if exp_header != act_header:
        return GateResult(
            name="csv_output_match",
            passed=False,
            detail=(
                f"CSV header mismatch.\n  expected: {exp_header}\n  actual:   {act_header}"
            ),
        )

    exp_sorted = sorted(exp_rows)
    act_sorted = sorted(act_rows)
    if exp_sorted != act_sorted:
        only_exp = [r for r in exp_sorted if r not in act_sorted][:5]
        only_act = [r for r in act_sorted if r not in exp_sorted][:5]
        return GateResult(
            name="csv_output_match",
            passed=False,
            detail=(
                f"CSV row contents differ. "
                f"expected_rows={len(exp_rows)}, actual_rows={len(act_rows)}. "
                f"Sample rows only in expected: {only_exp}. "
                f"Sample rows only in actual: {only_act}."
            ),
        )

    return GateResult(
        name="csv_output_match",
        passed=True,
        detail=f"CSV outputs match ({len(exp_rows)} rows, {len(exp_header)} columns).",
    )


def check_triggered_task_exists(
    expected_task_name: str,
    found_entry: dict[str, Any] | None,
) -> GateResult:
    """Verify the student created a Triggered Task with the expected name.

    Convention: a triggered task's name MUST be `<pipeline name> Task`.
    The convention is strict — a Triggered Task with any other name is
    treated as not-found and fails this gate, even if it correctly
    references the student's pipeline. See task_02_calculator/notes.md.
    """
    if found_entry is None:
        return GateResult(
            name="triggered_task_exists",
            passed=False,
            detail=(
                f"No Triggered Task named {expected_task_name!r} in the "
                f"student's project. The convention `<pipeline name> Task` "
                f"is strict — name the task exactly this."
            ),
        )
    return GateResult(
        name="triggered_task_exists",
        passed=True,
        detail=f"Triggered Task {expected_task_name!r} found.",
    )


def check_triggered_responses_match(
    scenarios: list[dict[str, Any]],
) -> GateResult:
    """Compare every scenario's expected response against the student's.

    `scenarios` is a list of dicts with keys:
      - name: scenario id (filesystem-safe)
      - expected_path: Path to cached solution response
      - student_path: Path to invoked student response
      - student_http_status: int | None
      - student_error: str | None

    Each scenario passes iff (a) the student invocation succeeded
    (status 200, no error) AND (b) the response body parses to JSON
    structurally equal to the expected body. Falls back to byte-equal
    when either side isn't valid JSON.

    Passes only when every scenario passes. Detail names each failing
    scenario and includes a short reason.
    """
    if not scenarios:
        return GateResult(
            name="triggered_task_responses_match",
            passed=False,
            detail="No scenarios registered in task.json.",
        )

    failures: list[str] = []
    for s in scenarios:
        name = s["name"]
        expected_path: Path = s["expected_path"]
        student_path: Path = s["student_path"]
        student_error = s.get("student_error")
        student_status = s.get("student_http_status")

        if student_error:
            failures.append(
                f"{name}: invocation failed ({student_error})"
            )
            continue
        if not expected_path.exists():
            failures.append(
                f"{name}: expected response missing at {expected_path}"
            )
            continue
        if not student_path.exists():
            failures.append(
                f"{name}: student response missing at {student_path}"
            )
            continue

        expected_bytes = expected_path.read_bytes()
        student_bytes = student_path.read_bytes()

        try:
            expected_json = json.loads(expected_bytes)
            student_json = json.loads(student_bytes)
            equal = expected_json == student_json
        except json.JSONDecodeError:
            equal = expected_bytes == student_bytes

        if not equal:
            preview_exp = _short_preview(expected_bytes)
            preview_act = _short_preview(student_bytes)
            failures.append(
                f"{name}: response differs — "
                f"expected={preview_exp} actual={preview_act}"
                + (f" (status {student_status})" if student_status not in (None, 200) else "")
            )

    if failures:
        return GateResult(
            name="triggered_task_responses_match",
            passed=False,
            detail=(
                f"{len(failures)} of {len(scenarios)} scenario(s) failed: "
                + " | ".join(failures)
            ),
        )

    return GateResult(
        name="triggered_task_responses_match",
        passed=True,
        detail=f"All {len(scenarios)} scenario response(s) match expected.",
    )


def _short_preview(data: bytes, max_chars: int = 120) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(data)} bytes, non-utf8>"
    text = text.replace("\n", "\\n").replace("\r", "\\r")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return repr(text)


def _read_csv(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    body = [tuple(r) for r in rows[1:]]
    return header, body
