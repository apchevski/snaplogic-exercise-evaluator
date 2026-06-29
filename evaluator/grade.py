"""Grading orchestrator for the `/grade` skill.

Two subcommands, both designed so the `/grade` skill prompt can stay small:

    python -m evaluator.grade plan <student> [--space <project_space>]
        Resolves the student's project, iterates registered exercises, runs the
        single-task evaluator for each, and writes a manifest JSON listing the
        outcome per task. Claude reads the manifest to decide which tasks still
        need AI judgment.

    python -m evaluator.grade report <student> [--space <project_space>]
        Reads the manifest plus each per-task evaluation.json and renders the
        aggregated `grades/<student>/report.md` plus a structured
        `grades/<student>/report.json` (same data, machine-readable for a future
        UI). After writing both, deletes the `.tmp/grades/<student>/` scratch
        directory so only the persistent files survive. Then silently rebuilds
        `frontend/dist/index.html` so the dashboard reflects the new student.

    python -m evaluator.grade sync-overall <student>
        Re-reads the rendered `## Overall` paragraph from report.md and writes
        it into `overall_summary` in report.json. Called by the /grade skill
        after Claude fills in the Overall paragraph in full grading mode.
        Also rebuilds `frontend/dist/index.html` so the dashboard picks up the new
        Overall summary.

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

from .config import GRADES_DIR, REPO_ROOT, TMP_DIR, load_settings
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


def _rel_to_repo(p: Path) -> str:
    """Render a path for the manifest, relative to the repo root (POSIX style).

    The manifest is consumed in two environments: inside the Docker container
    (working dir ``/app``) by `grade report`, and on the host by the AI tool
    that writes each ``evaluation.json``. An absolute path written inside the
    container (e.g. ``/app/.tmp/...``) is meaningless on the host, so we record
    repo-root-relative paths (``.tmp/grades/...``) and re-anchor them on read
    via :func:`_resolve_manifest_path`.
    """
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Outside the repo tree (shouldn't happen for scratch paths) — fall back
        # to the absolute path so the manifest still points somewhere valid.
        return p.as_posix()


def _resolve_manifest_path(stored: str) -> Path:
    """Re-anchor a manifest path against the repo root, independent of CWD.

    Accepts both the repo-root-relative form written by :func:`_rel_to_repo`
    and any legacy absolute path (older manifests stored absolutes); absolute
    paths are returned unchanged.
    """
    p = Path(stored)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _solution_pipeline_name(solution_pipeline_path: str) -> str:
    return solution_pipeline_path.rstrip("/").split("/")[-1]


def _rebuild_ui_silently() -> None:
    """Rebuild frontend/dist/index.html so the dashboard reflects the latest grades.

    Imported lazily to keep the grade CLI startup light and to avoid a hard
    coupling at module import time. UI build failures must never break a
    successful grading run — the dashboard is a convenience artifact, not a
    grading output.

    Set EVALUATOR_DISABLE_UI_REBUILD=1 to skip entirely — the cloud worker
    does this because the Lambda image filesystem is read-only and the React
    SPA replaces the static dashboard there.
    """
    import os

    if os.environ.get("EVALUATOR_DISABLE_UI_REBUILD", "").strip():
        return
    try:
        from .ui import cmd_build

        cmd_build(open_in_browser=False)
    except Exception as e:  # pragma: no cover - best-effort side effect
        print(
            f"WARNING: UI rebuild failed ({e!r}); run "
            f"`python -m evaluator.ui` manually to refresh frontend/dist/index.html.",
            file=sys.stderr,
        )


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
                    "ai_context_path": _rel_to_repo(per_task_ctx),
                    "evaluation_path": _rel_to_repo(per_task_eval),
                }
            )
        elif exit_code == 1 and per_task_eval.exists():
            entries.append(
                {
                    "slug": slug,
                    "status": "fail",
                    "student_pipeline_name": matched,
                    "evaluation_path": _rel_to_repo(per_task_eval),
                }
            )
        elif exit_code == 4 and per_task_eval.exists():
            # Deliverable not submitted (e.g. output_present 404)
            # → treated as MISSING (not graded, excluded from totals).
            eval_data = json.loads(per_task_eval.read_text(encoding="utf-8"))
            entries.append(
                {
                    "slug": slug,
                    "status": "missing",
                    "student_pipeline_name": matched,
                    "evaluation_path": _rel_to_repo(per_task_eval),
                    "reason": (
                        eval_data.get("failing_gate_detail")
                        or eval_data.get("summary")
                        or "Deliverable not submitted."
                    ),
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


MAX_POINTS_PER_EXERCISE = 10

_VERDICT_BADGES = {
    "pass": "✓ PASS",
    "fail": "✗ FAIL",
}


def _format_points(points: Any) -> str:
    """Render the `Points: X/10` line value.

    None means MISSING (or any unscored state) — displayed as `—`.
    Anything numeric clamps into [0, MAX_POINTS_PER_EXERCISE] for safety.
    """
    if points is None:
        return f"—/{MAX_POINTS_PER_EXERCISE}"
    try:
        p = int(points)
    except (TypeError, ValueError):
        return f"—/{MAX_POINTS_PER_EXERCISE}"
    p = max(0, min(p, MAX_POINTS_PER_EXERCISE))
    return f"{p}/{MAX_POINTS_PER_EXERCISE}"


def _render_task_section(slug: str, eval_data: dict[str, Any]) -> str:
    verdict = eval_data.get("verdict", "fail")
    badge = _VERDICT_BADGES.get(verdict, f"? {verdict}")
    points = eval_data.get("points")
    if verdict == "fail" and points is None:
        points = 0
    lines = [f"## {slug} — {badge}", ""]
    lines.append(f"**Points**: {_format_points(points)}")
    lines.append("")

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
    # Split deductions (cost points) from notes (mentioned only).
    deductions = [d for d in diffs if int(d.get("points_deducted") or 0) > 0]
    notes = [d for d in diffs if int(d.get("points_deducted") or 0) == 0]

    if deductions:
        total_ded = sum(int(d.get("points_deducted") or 0) for d in deductions)
        lines.append(f"**Deductions** (−{total_ded}):")
        for d in deductions:
            cost = int(d.get("points_deducted") or 0)
            area = d.get("area") or "(unspecified)"
            desc = d.get("description") or ""
            reasoning = d.get("reasoning") or ""
            tail = f" — {reasoning}" if reasoning else ""
            lines.append(f"- **−{cost}** · {area} — {desc}{tail}")
        lines.append("")

    if notes:
        lines.append("**Notes** (no deduction):")
        for d in notes:
            area = d.get("area") or "(unspecified)"
            desc = d.get("description") or ""
            reasoning = d.get("reasoning") or ""
            tail = f" — {reasoning}" if reasoning else ""
            lines.append(f"- {area} — {desc}{tail}")
        lines.append("")

    bonus = eval_data.get("bonus_question_answer")
    if bonus:
        lines.append(f"**Bonus question**: {bonus}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_entry_section(entry: dict[str, Any]) -> tuple[str, str, int | None]:
    """Render one manifest entry as (section_markdown, count_bucket, points).

    `points` is None for MISSING / NEEDS_PREP (not graded) and an int in
    [0, MAX_POINTS_PER_EXERCISE] otherwise. The caller uses it to compute
    the per-student total.
    """
    slug = entry["slug"]
    status = entry["status"]
    if status == "needs_prep":
        return (
            f"## {slug} — ⏳ NEEDS PREP\n\n"
            f"**Points**: {_format_points(None)}\n\n"
            f"**Reason**: {entry.get('reason', 'Solution cache not ready.')}\n\n"
            f"Run `/prep` to bootstrap or refresh, then re-run `/grade`.",
            "needs_prep",
            None,
        )
    if status == "missing":
        return (
            f"## {slug} — ⊘ MISSING\n\n"
            f"**Points**: {_format_points(None)}\n\n"
            f"**Reason**: {entry.get('reason', 'No matching pipeline.')}",
            "missing",
            None,
        )
    if status == "config_error":
        return (
            f"## {slug} — ✗ CONFIG ERROR\n\n"
            f"**Points**: {_format_points(0)}\n\n"
            f"**Reason**: {entry.get('reason', 'Unknown error.')}",
            "fail",
            0,
        )
    eval_path = _resolve_manifest_path(entry["evaluation_path"])
    if not eval_path.exists():
        return (
            f"## {slug} — ✗ MISSING EVALUATION\n\n"
            f"**Points**: {_format_points(0)}\n\n"
            f"Expected `{eval_path}` after AI judgment but it was not written.",
            "fail",
            0,
        )
    eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
    verdict = eval_data.get("verdict", "fail")
    bucket = verdict if verdict in {"pass", "fail"} else "fail"
    pts = eval_data.get("points")
    if verdict == "fail" and pts is None:
        pts = 0
    if pts is not None:
        try:
            pts = max(0, min(int(pts), MAX_POINTS_PER_EXERCISE))
        except (TypeError, ValueError):
            pts = 0
    return _render_task_section(slug, eval_data), bucket, pts


def _build_task_data(entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Convert one manifest entry into a JSON-shaped task record.

    Mirrors the cases handled by `_render_entry_section` so report.md and
    report.json describe the same outcome for the same entry. Returns
    `(task_data, count_bucket)`; the bucket matches `_render_entry_section`'s
    so callers can use either function as the source of truth for counts.
    """
    slug = entry["slug"]
    status = entry["status"]

    if status == "needs_prep":
        return (
            {
                "slug": slug,
                "status": "needs_prep",
                "verdict": None,
                "reason": entry.get("reason", "Solution cache not ready."),
            },
            "needs_prep",
        )
    if status == "missing":
        return (
            {
                "slug": slug,
                "status": "missing",
                "verdict": None,
                "reason": entry.get("reason", "No matching pipeline."),
            },
            "missing",
        )
    if status == "config_error":
        return (
            {
                "slug": slug,
                "status": "config_error",
                "verdict": None,
                "reason": entry.get("reason", "Unknown error."),
            },
            "fail",
        )

    eval_path = _resolve_manifest_path(entry["evaluation_path"])
    if not eval_path.exists():
        return (
            {
                "slug": slug,
                "status": "missing_evaluation",
                "verdict": None,
                "reason": f"Expected {eval_path.name} after AI judgment but it was not written.",
            },
            "fail",
        )

    eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
    verdict = eval_data.get("verdict", "fail")
    bucket = verdict if verdict in {"pass", "fail"} else "fail"
    pts = eval_data.get("points")
    if verdict == "fail" and pts is None:
        pts = 0
    if pts is not None:
        try:
            pts = max(0, min(int(pts), MAX_POINTS_PER_EXERCISE))
        except (TypeError, ValueError):
            pts = 0
    return (
        {
            "slug": slug,
            "status": "evaluated",
            "student_pipeline_name": entry.get("student_pipeline_name"),
            "verdict": verdict,
            "points": pts,
            "summary": eval_data.get("summary"),
            "differences": eval_data.get("differences") or [],
            "bonus_question_answer": eval_data.get("bonus_question_answer"),
            "failing_gate": eval_data.get("failing_gate"),
            "failing_gate_detail": eval_data.get("failing_gate_detail"),
        },
        bucket,
    )


def _counts_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "missing": 0, "needs_prep": 0}
    for t in tasks:
        status = t.get("status")
        if status == "needs_prep":
            counts["needs_prep"] += 1
        elif status == "missing":
            counts["missing"] += 1
        elif status in {"config_error", "missing_evaluation"}:
            counts["fail"] += 1
        else:
            verdict = t.get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            else:
                counts["fail"] += 1
    return counts


def _sum_points(tasks: list[dict[str, Any]]) -> int:
    """Sum task points for the per-student total numerator.

    MISSING / NEEDS_PREP tasks store `points: None`; they contribute 0
    to the numerator but still count in the denominator
    `(total exercises) × MAX_POINTS_PER_EXERCISE`.
    """
    total = 0
    for t in tasks:
        p = t.get("points")
        if p is None:
            continue
        try:
            total += max(0, min(int(p), MAX_POINTS_PER_EXERCISE))
        except (TypeError, ValueError):
            pass
    return total


def _write_report_json(
    out_path: Path,
    manifest: dict[str, Any],
    tasks: list[dict[str, Any]],
    overall_summary: str | None = None,
) -> None:
    """Write the full structured report.json (full grading mode)."""
    counts = _counts_from_tasks(tasks)
    total_exercises = sum(counts.values())
    payload: dict[str, Any] = {
        "student": manifest["student"],
        "org": manifest["org"],
        "project_space": manifest["project_space"],
        "student_project_path": manifest["student_project_path"],
        "graded_at": manifest["generated_at"],
        "counts": {**counts, "total": total_exercises},
        "points_earned": _sum_points(tasks),
        "points_possible": total_exercises * MAX_POINTS_PER_EXERCISE,
        "max_points_per_exercise": MAX_POINTS_PER_EXERCISE,
        "overall_summary": overall_summary,
        "tasks": tasks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _update_report_json_in_place(
    out_path: Path,
    task_data: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    """Replace one task entry in report.json (single-task mode).

    If report.json already exists, the target task is replaced (or appended
    if not present), and `counts` is recomputed from the merged task list.
    `overall_summary` is preserved as-is — a single-task re-grade does not
    claim to have refreshed the whole submission.

    If report.json doesn't exist yet, a minimal one is written containing
    just this task.
    """
    slug = task_data["slug"]

    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        tasks: list[dict[str, Any]] = list(existing.get("tasks") or [])
        replaced = False
        for i, t in enumerate(tasks):
            if t.get("slug") == slug:
                tasks[i] = task_data
                replaced = True
                break
        if not replaced:
            tasks.append(task_data)
        counts = _counts_from_tasks(tasks)
        total_exercises = sum(counts.values())
        existing["tasks"] = tasks
        existing["counts"] = {**counts, "total": total_exercises}
        existing["points_earned"] = _sum_points(tasks)
        existing["points_possible"] = total_exercises * MAX_POINTS_PER_EXERCISE
        existing["max_points_per_exercise"] = MAX_POINTS_PER_EXERCISE
        out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return

    counts = _counts_from_tasks([task_data])
    total_exercises = sum(counts.values())
    payload: dict[str, Any] = {
        "student": manifest["student"],
        "org": manifest["org"],
        "project_space": manifest["project_space"],
        "student_project_path": manifest["student_project_path"],
        "graded_at": manifest["generated_at"],
        "single_task_only": slug,
        "counts": {**counts, "total": total_exercises},
        "points_earned": _sum_points([task_data]),
        "points_possible": total_exercises * MAX_POINTS_PER_EXERCISE,
        "max_points_per_exercise": MAX_POINTS_PER_EXERCISE,
        "overall_summary": None,
        "tasks": [task_data],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _extract_overall_from_md(md_text: str) -> str | None:
    """Pull the Overall paragraph out of report.md.

    Returns None when the section is absent (single-task report) or still
    holding the TODO placeholder (full mode before Claude fills it in).
    """
    marker = "\n## Overall\n\n"
    idx = md_text.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = md_text.find("\n\n---\n\n", start)
    if end < 0:
        end = len(md_text)
    overall = md_text[start:end].strip()
    if not overall or overall.startswith("<!-- TODO"):
        return None
    return overall


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
    new_section, _, _ = _render_entry_section(entry)
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

        json_path = _student_report_dir(student) / "report.json"
        task_data, _ = _build_task_data(entry)
        _update_report_json_in_place(json_path, task_data, manifest)

        # Print result line for the one task.
        status = entry["status"]
        if status == "needs_prep":
            print(f"  {task_slug} -> NEEDS_PREP")
        elif status == "missing":
            print(f"  {task_slug} -> MISSING")
        elif status == "config_error":
            print(f"  {task_slug} -> CONFIG_ERROR")
        else:
            eval_path = _resolve_manifest_path(entry["evaluation_path"])
            if eval_path.exists():
                v = json.loads(eval_path.read_text(encoding="utf-8")).get("verdict", "?")
                print(f"  {task_slug} -> {v}")
            else:
                print(f"  {task_slug} -> NO_EVALUATION")
        print(f"Report updated in place: {out_path}")
        print(f"JSON updated: {json_path}")

        tmp_dir = _student_dir(student)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"Cleaned up scratch dir: {tmp_dir}")
        _rebuild_ui_silently()
        return 0

    counts = {
        "pass": 0,
        "fail": 0,
        "missing": 0,
        "needs_prep": 0,
    }
    sections: list[str] = []
    tasks_json: list[dict[str, Any]] = []
    points_earned = 0

    for entry in entries:
        section, bucket, pts = _render_entry_section(entry)
        counts[bucket] = counts.get(bucket, 0) + 1
        sections.append(section)
        task_data, _ = _build_task_data(entry)
        tasks_json.append(task_data)
        if pts is not None:
            points_earned += pts

    total = len(entries)
    points_possible = total * MAX_POINTS_PER_EXERCISE
    total_line = (
        f"- **Total**: {points_earned}/{points_possible} points"
        if total
        else "- **Total**: — no exercises"
    )
    header = [
        f"# Grade report — {student}",
        "",
        f"- **Project**: `{manifest['student_project_path']}`",
        f"- **Date**: {manifest['generated_at']}",
        f"- **Exercises evaluated**: {total}",
        (
            f"- **Pass**: {counts['pass']} · "
            f"**Fail**: {counts['fail']} · "
            f"**Missing**: {counts['missing']} · "
            f"**Needs prep**: {counts['needs_prep']}"
        ),
        total_line,
        "",
        "## Overall",
        "",
        "<!-- TODO Claude: SHORT (1-2 sentence) GENERAL synthesis of the whole "
        "submission. Lead with pass/fail count + total points, then characterize "
        "overall performance / recurring themes in general terms. NO specific "
        "tasks/slugs. NO recommendations or suggestions for improvement. "
        "Replace this comment with the paragraph. -->",
    ]

    body = ("\n\n---\n\n").join(sections)
    report = "\n".join(header) + "\n\n---\n\n" + body + "\n"

    out_path = _student_report_dir(student) / "report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    json_path = _student_report_dir(student) / "report.json"
    _write_report_json(json_path, manifest, tasks_json, overall_summary=None)

    print(f"Report written: {out_path}")
    print(f"JSON written:   {json_path}")
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
            eval_path = _resolve_manifest_path(entry["evaluation_path"])
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
    _rebuild_ui_silently()
    return 0


# ---------- `sync-overall` subcommand ----------


def cmd_sync_overall(student: str) -> int:
    """Read ## Overall from report.md and write it to overall_summary in report.json.

    Called by the /grade skill after Claude has filled in the Overall
    paragraph (full mode only). Idempotent; safe to re-run.
    """
    report_dir = _student_report_dir(student)
    md_path = report_dir / "report.md"
    json_path = report_dir / "report.json"

    if not md_path.exists():
        print(f"ERROR: No report.md at {md_path}", file=sys.stderr)
        return 2
    if not json_path.exists():
        print(
            f"ERROR: No report.json at {json_path}. "
            f"Run `python -m evaluator.grade report \"{student}\"` first.",
            file=sys.stderr,
        )
        return 2

    overall = _extract_overall_from_md(md_path.read_text(encoding="utf-8"))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["overall_summary"] = overall
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    if overall is None:
        print(
            f"WARNING: ## Overall in {md_path} is empty or still the TODO "
            f"placeholder. overall_summary set to null in {json_path}."
        )
    else:
        print(f"Synced ## Overall ({len(overall)} chars) into {json_path}")
    _rebuild_ui_silently()
    return 0


# ---------- CLI entry point ----------


def main(argv: list[str] | None = None) -> int:
    # Gate details and SnapLogic pipeline names carry non-ASCII text (e.g. an
    # en-dash in a task name, or accented characters in sample output rows).
    # The default Windows console is cp1252 and raises UnicodeEncodeError on
    # those, which would abort an otherwise-healthy run mid-print. Force UTF-8
    # with errors="replace" so progress output never crashes the orchestrator.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

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
        help="Render aggregated report.md + report.json from the manifest + per-task evaluations.",
    )
    p_report.add_argument("student")
    p_report.add_argument("--space", dest="project_space", default=None)
    p_report.add_argument(
        "--task",
        dest="task_slug",
        default=None,
        help=(
            "Update only one task's section in the existing report.md in place "
            "(or create a single-task report if none exists). report.json is "
            "updated in lockstep for the same task."
        ),
    )

    p_sync = subparsers.add_parser(
        "sync-overall",
        help="Copy the ## Overall paragraph from report.md into overall_summary in report.json.",
    )
    p_sync.add_argument("student")

    args = parser.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args.student, args.project_space, args.task_slug)
    if args.cmd == "report":
        return cmd_report(args.student, args.project_space, args.task_slug)
    if args.cmd == "sync-overall":
        return cmd_sync_overall(args.student)
    parser.error(f"Unknown subcommand: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
