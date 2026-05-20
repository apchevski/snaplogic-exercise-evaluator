"""Grading orchestrator for the `/grade` skill.

Two subcommands, both designed so the `/grade` skill prompt can stay small:

    python -m evaluator.grade plan <student> [--space <project_space>]
        Resolves the student's project, iterates registered exercises, runs the
        single-task evaluator for each, and writes a manifest JSON listing the
        outcome per task. Claude reads the manifest to decide which tasks still
        need AI judgment.

    python -m evaluator.grade report <student> [--space <project_space>]
        Reads the manifest plus each per-task evaluation.json and renders the
        aggregated `grades/<student>/report.md`. After writing the report,
        deletes the `.tmp/grades/<student>/` scratch directory so only the
        persistent report.md survives.

This module never calls an LLM. Judgment still lives in the `/grade` skill.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import GRADES_DIR, TMP_DIR, load_settings
from .evaluate import run_evaluation
from .name_match import names_match
from .pipeline_fetch import SolutionNotReadyError
from .snaplogic_client import SnapLogicClient
from .tasks import list_exercise_folders, list_tasks, load_task


# ---------- shared helpers ----------


def _student_dir(student: str) -> Path:
    """Scratch directory under .tmp for a single grading run. Deleted after report."""
    return TMP_DIR / "grades" / student


def _student_report_dir(student: str) -> Path:
    """Persistent home for the rendered report. Survives between runs."""
    return GRADES_DIR / student


def _manifest_path(student: str) -> Path:
    return _student_dir(student) / "manifest.json"


def _solution_pipeline_name(solution_pipeline_path: str) -> str:
    return solution_pipeline_path.rstrip("/").split("/")[-1]


def _find_student_pipeline(
    assets: list[dict[str, Any]],
    target_name: str,
) -> tuple[str | None, bool]:
    """Return (matched_pipeline_name, is_fuzzy). None if no plausible match.

    Name comparison is exact except dash glyphs (en/em-dash count as
    hyphen-minus) — see `evaluator.name_match`. `is_fuzzy` is kept in the
    return signature for callers but is always False today: dash-tolerance
    is the only allowed deviation, not a fuzzy heuristic.
    """
    pipelines = [a for a in assets if a.get("asset_type") == "Pipeline"]
    for a in pipelines:
        if names_match(a.get("name", ""), target_name):
            return a["name"], False
    return None, False


# ---------- `plan` subcommand ----------


def cmd_plan(
    student: str,
    project_space: str | None,
    task_slug: str | None = None,
) -> int:
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Copy .env.example to .env and fill in SnapLogic credentials.",
            file=sys.stderr,
        )
        return 2

    org = settings.org_name
    ps = project_space or settings.student_project_space_name
    student_project_path = f"{org}/{ps}/{student}"

    registered = list_tasks()
    all_folders = list_exercise_folders()
    if not all_folders:
        print(
            "ERROR: No exercise folders found under exercises/.",
            file=sys.stderr,
        )
        return 2

    if task_slug is not None:
        if task_slug not in all_folders:
            print(
                f"ERROR: No exercise folder named {task_slug!r} under exercises/. "
                f"Known: {all_folders}",
                file=sys.stderr,
            )
            return 2
        # Single-task mode: ignore other registered/unregistered folders entirely.
        was_registered = task_slug in registered
        registered = [task_slug] if was_registered else []
        unregistered = [] if was_registered else [task_slug]
    else:
        registered_set = set(registered)
        unregistered = [f for f in all_folders if f not in registered_set]

    entries: list[dict[str, Any]] = []
    for folder in unregistered:
        reason = (
            f"No task.json in exercises/{folder}/. "
            f"Run `/prep` to bootstrap it."
        )
        entries.append({"slug": folder, "status": "needs_prep", "reason": reason})
        print(f"[{folder}] NEEDS PREP — no task.json")

    with SnapLogicClient(settings) as client:
        try:
            assets = client.list_assets(org, ps, student)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(
                    f"ERROR: Student project not found: {student_project_path}",
                    file=sys.stderr,
                )
                print(
                    "Check spelling or pass --space to override the project space.",
                    file=sys.stderr,
                )
                return 2
            raise

    print(f"Student project: {student_project_path} ({len(assets)} assets)")
    print(f"Exercises registered: {len(registered)} (unregistered folders: {len(unregistered)})")
    print("-" * 60)

    for slug in registered:
        try:
            task = load_task(slug)
        except FileNotFoundError as e:
            entries.append({"slug": slug, "status": "config_error", "reason": str(e)})
            continue

        target = _solution_pipeline_name(task.solution_pipeline_path)
        matched, _ = _find_student_pipeline(assets, target)
        if matched is None:
            entries.append(
                {
                    "slug": slug,
                    "status": "missing",
                    "reason": f"No pipeline matching {target!r} in {student_project_path}",
                }
            )
            print(f"[{slug}] MISSING — no pipeline matching {target!r}")
            continue

        student_pipeline_path = f"{org}/{ps}/{student}/{matched}"
        print(f"\n[{slug}] running evaluator against {matched!r} ...")
        try:
            exit_code = run_evaluation(slug, student_pipeline_path, student_name=student)
        except SolutionNotReadyError as e:
            reason = f"{e.status}: {e.reason}"
            entries.append({"slug": slug, "status": "needs_prep", "reason": reason})
            print(f"[{slug}] NEEDS PREP — {reason}")
            continue

        per_task_eval = _student_dir(student) / slug / "evaluation.json"
        per_task_ctx = _student_dir(student) / slug / "ai_context.json"

        if exit_code == 0 and per_task_ctx.exists():
            entries.append(
                {
                    "slug": slug,
                    "status": "ready_for_ai",
                    "student_pipeline_name": matched,
                    "ai_context_path": str(per_task_ctx),
                    "evaluation_path": str(per_task_eval),
                }
            )
        elif exit_code == 1 and per_task_eval.exists():
            entries.append(
                {
                    "slug": slug,
                    "status": "fail",
                    "student_pipeline_name": matched,
                    "evaluation_path": str(per_task_eval),
                }
            )
        else:
            entries.append(
                {
                    "slug": slug,
                    "status": "config_error",
                    "reason": f"evaluator exit_code={exit_code}",
                }
            )

    manifest = {
        "student": student,
        "org": org,
        "project_space": ps,
        "student_project_path": student_project_path,
        "generated_at": _dt.date.today().isoformat(),
        "entries": entries,
    }
    out_path = _manifest_path(student)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"Manifest written: {out_path}")
    ready = [e for e in entries if e["status"] == "ready_for_ai"]
    needs_prep = [e for e in entries if e["status"] == "needs_prep"]
    if ready:
        print(f"{len(ready)} task(s) READY_FOR_AI_REVIEW:")
        for e in ready:
            print(f"  - {e['slug']}  (ai_context: {e['ai_context_path']})")
    else:
        print("No tasks require AI judgment — all gates resolved deterministically.")
    if needs_prep:
        print()
        print(f"{len(needs_prep)} folder(s) NEED PREP — run `/prep` first:")
        for e in needs_prep:
            print(f"  - {e['slug']}: {e['reason']}")
    return 0


# ---------- `report` subcommand ----------


_VERDICT_BADGES = {
    "pass": "✓ PASS",
    "pass_with_minor_issues": "⚠ PASS with minor issues",
    "fail": "✗ FAIL",
}

_SEVERITY_DOTS = {
    "major": "🔴",
    "minor": "🟡",
    "cosmetic": "🔵",
}


def _render_task_section(slug: str, eval_data: dict[str, Any]) -> str:
    verdict = eval_data.get("verdict", "fail")
    badge = _VERDICT_BADGES.get(verdict, f"? {verdict}")
    lines = [f"## {slug} — {badge}", ""]

    summary = eval_data.get("summary") or "(no summary)"
    lines.append(f"**Summary**: {summary}")
    lines.append("")

    if eval_data.get("failing_gate"):
        lines.append(
            f"**Failing gate**: `{eval_data['failing_gate']}`"
        )
        detail = eval_data.get("failing_gate_detail") or ""
        if detail:
            lines.append("")
            lines.append("```")
            lines.append(detail.rstrip())
            lines.append("```")
        lines.append("")

    diffs = eval_data.get("differences") or []
    if diffs:
        lines.append("**Differences**:")
        for d in diffs:
            sev = (d.get("severity") or "").lower()
            dot = _SEVERITY_DOTS.get(sev, "•")
            area = d.get("area") or "(unspecified)"
            desc = d.get("description") or ""
            reasoning = d.get("reasoning") or ""
            tail = f" — {reasoning}" if reasoning else ""
            lines.append(f"- {dot} **[{sev or 'note'}]** {area} — {desc}{tail}")
        lines.append("")

    bonus = eval_data.get("bonus_question_answer")
    if bonus:
        lines.append(f"**Bonus question**: {bonus}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_entry_section(entry: dict[str, Any]) -> tuple[str, str]:
    """Render one manifest entry as (section_markdown, count_bucket)."""
    slug = entry["slug"]
    status = entry["status"]
    if status == "needs_prep":
        return (
            f"## {slug} — ⏳ NEEDS PREP\n\n"
            f"**Reason**: {entry.get('reason', 'Solution cache not ready.')}\n\n"
            f"Run `/prep` to bootstrap or refresh, then re-run `/grade`.",
            "needs_prep",
        )
    if status == "missing":
        return (
            f"## {slug} — ⊘ MISSING\n\n"
            f"**Reason**: {entry.get('reason', 'No matching pipeline.')}",
            "missing",
        )
    if status == "config_error":
        return (
            f"## {slug} — ✗ CONFIG ERROR\n\n"
            f"**Reason**: {entry.get('reason', 'Unknown error.')}",
            "fail",
        )
    eval_path = Path(entry["evaluation_path"])
    if not eval_path.exists():
        return (
            f"## {slug} — ✗ MISSING EVALUATION\n\n"
            f"Expected `{eval_path}` after AI judgment but it was not written.",
            "fail",
        )
    eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
    verdict = eval_data.get("verdict", "fail")
    bucket = verdict if verdict in {"pass", "pass_with_minor_issues", "fail"} else "fail"
    return _render_task_section(slug, eval_data), bucket


def _split_report_sections(text: str) -> tuple[str, list[str]]:
    """Split a rendered report.md into (head_block, [task_sections]).

    Sections are separated by '\\n\\n---\\n\\n'. The head block contains
    the title, metadata bullets, and the Overall section. Returns the
    full text as head_block and [] if no separator is found.
    """
    parts = text.split("\n\n---\n\n")
    if len(parts) <= 1:
        return text, []
    return parts[0], parts[1:]


def _section_matches_slug(section: str, slug: str) -> bool:
    head = section.lstrip().splitlines()[0] if section.strip() else ""
    return head.startswith(f"## {slug} ") or head == f"## {slug}"


def _update_report_in_place(
    out_path: Path,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    student: str,
) -> None:
    """Replace one task's section in the existing report.md (or create one).

    Header, counts, date, and Overall are intentionally left untouched —
    a single-task re-grade should not claim to have refreshed everything.
    If no report exists yet, a minimal single-task report is written
    instead.
    """
    new_section, _ = _render_entry_section(entry)
    slug = entry["slug"]

    if out_path.exists():
        existing = out_path.read_text(encoding="utf-8")
        head_block, sections = _split_report_sections(existing)
        updated_sections: list[str] = []
        replaced = False
        for sec in sections:
            if not replaced and _section_matches_slug(sec, slug):
                updated_sections.append(new_section)
                replaced = True
            else:
                updated_sections.append(sec)
        if not replaced:
            updated_sections.append(new_section)
        merged = head_block + "\n\n---\n\n" + "\n\n---\n\n".join(updated_sections) + "\n"
        out_path.write_text(merged, encoding="utf-8")
        return

    # No existing report — produce a minimal single-task one. Counts are
    # omitted on purpose (we don't know about other tasks).
    header_lines = [
        f"# Grade report — {student}",
        "",
        f"- **Project**: `{manifest['student_project_path']}`",
        f"- **Date**: {manifest['generated_at']}",
        f"- **Single-task grading**: `{slug}`",
        "",
    ]
    report = "\n".join(header_lines) + "\n\n---\n\n" + new_section + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")


def cmd_report(
    student: str,
    project_space: str | None,
    task_slug: str | None = None,
) -> int:
    manifest_path = _manifest_path(student)
    if not manifest_path.exists():
        print(
            f"ERROR: No manifest at {manifest_path}. Run "
            f"`python -m evaluator.grade plan \"{student}\"` first.",
            file=sys.stderr,
        )
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["entries"]

    if task_slug is not None:
        entry = next((e for e in entries if e["slug"] == task_slug), None)
        if entry is None:
            print(
                f"ERROR: Manifest has no entry for {task_slug!r}. "
                f"Available: {[e['slug'] for e in entries]}",
                file=sys.stderr,
            )
            return 2
        out_path = _student_report_dir(student) / "report.md"
        _update_report_in_place(out_path, entry, manifest, student)

        # Print result line for the one task.
        status = entry["status"]
        if status == "needs_prep":
            print(f"  {task_slug} -> NEEDS_PREP")
        elif status == "missing":
            print(f"  {task_slug} -> MISSING")
        elif status == "config_error":
            print(f"  {task_slug} -> CONFIG_ERROR")
        else:
            eval_path = Path(entry["evaluation_path"])
            if eval_path.exists():
                v = json.loads(eval_path.read_text(encoding="utf-8")).get("verdict", "?")
                print(f"  {task_slug} -> {v}")
            else:
                print(f"  {task_slug} -> NO_EVALUATION")
        print(f"Report updated in place: {out_path}")

        tmp_dir = _student_dir(student)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"Cleaned up scratch dir: {tmp_dir}")
        return 0

    counts = {
        "pass": 0,
        "pass_with_minor_issues": 0,
        "fail": 0,
        "missing": 0,
        "needs_prep": 0,
    }
    sections: list[str] = []

    for entry in entries:
        section, bucket = _render_entry_section(entry)
        counts[bucket] = counts.get(bucket, 0) + 1
        sections.append(section)

    total = len(entries)
    header = [
        f"# Grade report — {student}",
        "",
        f"- **Project**: `{manifest['student_project_path']}`",
        f"- **Date**: {manifest['generated_at']}",
        f"- **Exercises evaluated**: {total}",
        (
            f"- **Pass**: {counts['pass']} · "
            f"**Pass with minor issues**: {counts['pass_with_minor_issues']} · "
            f"**Fail**: {counts['fail']} · "
            f"**Missing**: {counts['missing']} · "
            f"**Needs prep**: {counts['needs_prep']}"
        ),
        "",
        "## Overall",
        "",
        "<!-- TODO Claude: one-paragraph synthesis across all tasks. "
        "Flag patterns (e.g. \"consistently swapping filter/sort order\"). "
        "Replace this comment with the paragraph. -->",
    ]

    body = ("\n\n---\n\n").join(sections)
    report = "\n".join(header) + "\n\n---\n\n" + body + "\n"

    out_path = _student_report_dir(student) / "report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(f"Report written: {out_path}")
    print()
    print("Per-task results:")
    for entry in entries:
        slug = entry["slug"]
        status = entry["status"]
        if status == "needs_prep":
            print(f"  {slug} -> NEEDS_PREP")
        elif status == "missing":
            print(f"  {slug} -> MISSING")
        elif status == "config_error":
            print(f"  {slug} -> CONFIG_ERROR")
        else:
            eval_path = Path(entry["evaluation_path"])
            if eval_path.exists():
                v = json.loads(eval_path.read_text(encoding="utf-8")).get("verdict", "?")
                print(f"  {slug} -> {v}")
            else:
                print(f"  {slug} -> NO_EVALUATION")

    # Cleanup: .tmp is scratch space. The persistent report.md is already
    # written under grades/<student>/; drop everything else.
    tmp_dir = _student_dir(student)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"Cleaned up scratch dir: {tmp_dir}")
    return 0


# ---------- CLI entry point ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evaluator.grade",
        description="Orchestrate /grade: plan tasks, then render the report.",
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    p_plan = subparsers.add_parser(
        "plan",
        help="Resolve student project, run all hard gates, write manifest.",
    )
    p_plan.add_argument("student", help="Student name (project name within the project space).")
    p_plan.add_argument(
        "--space",
        dest="project_space",
        default=None,
        help="Override SNAPLOGIC_STUDENT_PROJECT_SPACE.",
    )
    p_plan.add_argument(
        "--task",
        dest="task_slug",
        default=None,
        help="Limit the run to a single exercise folder (slug under exercises/).",
    )

    p_report = subparsers.add_parser(
        "report",
        help="Render aggregated report.md from the manifest + per-task evaluations.",
    )
    p_report.add_argument("student")
    p_report.add_argument("--space", dest="project_space", default=None)
    p_report.add_argument(
        "--task",
        dest="task_slug",
        default=None,
        help=(
            "Update only one task's section in the existing report.md in place "
            "(or create a single-task report if none exists)."
        ),
    )

    args = parser.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args.student, args.project_space, args.task_slug)
    if args.cmd == "report":
        return cmd_report(args.student, args.project_space, args.task_slug)
    parser.error(f"Unknown subcommand: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
