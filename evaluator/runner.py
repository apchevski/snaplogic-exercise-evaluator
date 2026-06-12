"""Run a full grading pass in-process: hard gates → AI judge → report.

This is the headless replacement for the deleted `/grade` skill loop. The
deterministic halves are reused verbatim from `evaluator.grade`:

    plan   — cmd_plan resolves the student project, runs every hard gate,
             and writes `.tmp/grades/<student>/manifest.json`.
    judge  — every manifest entry with status `ready_for_ai` has its
             `ai_context.json` bundle sent to `AIJudge.judge_exercise`,
             which writes the `evaluation.json` the renderer expects.
    report — cmd_report renders `grades/<student>/report.md` + `report.json`
             and cleans up the scratch dir.
    overall— full runs get one extra small judge call that fills in the
             `## Overall` paragraph (replacing the old sync-overall step).

Used by the cloud worker Lambda (`backend/src/worker.py`) and by the local
dev CLI (`python -m evaluator run <student>`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import grade
from .ai_judge import AIJudge, JudgeUsage
from .config import GRADES_DIR, TMP_DIR


class GradeRunError(RuntimeError):
    """A grading run failed before producing a report."""


@dataclass
class GradeRunResult:
    student: str
    report_dir: Path
    report_md_path: Path
    report_json_path: Path
    report: dict[str, Any]
    judged_count: int
    usage: JudgeUsage = field(default_factory=JudgeUsage)

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.report.get("counts") or {})

    @property
    def points_earned(self) -> int:
        return int(self.report.get("points_earned") or 0)

    @property
    def points_possible(self) -> int:
        return int(self.report.get("points_possible") or 0)


def _manifest_path(student: str) -> Path:
    return TMP_DIR / "grades" / student / "manifest.json"


def _replace_overall_in_md(md_text: str, overall: str) -> str:
    """Swap the `## Overall` section body (TODO placeholder or stale text)."""
    marker = "\n## Overall\n\n"
    idx = md_text.find(marker)
    if idx < 0:
        return md_text
    start = idx + len(marker)
    end = md_text.find("\n\n---\n\n", start)
    if end < 0:
        end = len(md_text)
    return md_text[:start] + overall.strip() + md_text[end:]


def run_grade(
    student: str,
    *,
    project_space: str | None = None,
    task_slug: str | None = None,
    judge: AIJudge | None = None,
    plan_fn: Callable[[str, str | None, str | None], int] | None = None,
    report_fn: Callable[[str, str | None, str | None], int] | None = None,
) -> GradeRunResult:
    """Grade one student end to end and return the structured result.

    `plan_fn` / `report_fn` default to the real `evaluator.grade` commands;
    tests inject fakes so no SnapLogic access is needed.
    """
    plan_fn = plan_fn or grade.cmd_plan
    report_fn = report_fn or grade.cmd_report

    rc = plan_fn(student, project_space, task_slug)
    if rc != 0:
        raise GradeRunError(
            f"Hard-gate planning failed for {student!r} (exit code {rc}). "
            "See the run log for the underlying error."
        )

    manifest_file = _manifest_path(student)
    if not manifest_file.exists():
        raise GradeRunError(f"Planner wrote no manifest at {manifest_file}.")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    # --- AI judgment for every bundle the gates marked ready ---
    ready = [e for e in manifest.get("entries", []) if e.get("status") == "ready_for_ai"]
    usage = JudgeUsage()
    if ready:
        judge = judge or AIJudge()
        usage.model = judge.model
        for entry in ready:
            bundle_path = grade._resolve_manifest_path(entry["ai_context_path"])
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            evaluation, call_usage = judge.judge_exercise(bundle)
            usage.merge(call_usage)
            eval_path = grade._resolve_manifest_path(entry["evaluation_path"])
            eval_path.parent.mkdir(parents=True, exist_ok=True)
            eval_path.write_text(
                json.dumps(evaluation, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(
                f"[{entry['slug']}] judged -> {evaluation['verdict']} "
                f"({evaluation['points']}/10)"
            )

    # --- render report.md + report.json (also deletes the scratch dir) ---
    rc = report_fn(student, project_space, task_slug)
    if rc != 0:
        raise GradeRunError(f"Report rendering failed for {student!r} (exit code {rc}).")

    report_dir = GRADES_DIR / student
    report_md_path = report_dir / "report.md"
    report_json_path = report_dir / "report.json"
    report = json.loads(report_json_path.read_text(encoding="utf-8"))

    # --- Overall paragraph (full runs only; single-task re-grades keep the
    #     existing Overall untouched, same as the old skill behavior) ---
    if task_slug is None:
        judge = judge or AIJudge()
        if not usage.calls:
            usage.model = judge.model
        overall, overall_usage = judge.overall_summary(report)
        usage.merge(overall_usage)
        if overall:
            md = report_md_path.read_text(encoding="utf-8")
            report_md_path.write_text(
                _replace_overall_in_md(md, overall), encoding="utf-8"
            )
            report["overall_summary"] = overall
            report_json_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    return GradeRunResult(
        student=student,
        report_dir=report_dir,
        report_md_path=report_md_path,
        report_json_path=report_json_path,
        report=report,
        judged_count=len(ready),
        usage=usage,
    )
