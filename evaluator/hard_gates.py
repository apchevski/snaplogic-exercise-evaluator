"""Deterministic pre-AI checks that can short-circuit an evaluation.

These are 'hard rules' — if any fail, the exercise is automatically
failed and we do NOT spend tokens asking the AI.

Current hard gates (see exercises/general_evaluation_rules.md):
  1. Student pipeline name must exactly match solution pipeline name.
  2. Student CSV output must exactly match solution CSV output (rows,
     not byte order — we sort+compare so trivial encoding differences
     don't false-fail).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


def check_pipeline_name_match(solution_name: str, student_name: str) -> GateResult:
    passed = solution_name == student_name
    if passed:
        detail = f"Pipeline name matches: {solution_name!r}"
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


def _read_csv(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    body = [tuple(r) for r in rows[1:]]
    return header, body
