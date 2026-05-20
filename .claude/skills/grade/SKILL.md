---
name: grade
description: Grade a SnapLogic student's exercises by comparing each of their pipelines against the official solution. Usage — /grade <student name>  OR  /grade --space <project space> <student name>  OR  /grade <student name> --task <slug> (grade only one exercise, updating that task's section in the existing report in place). Iterates every exercise registered in exercises/<slug>/task.json, runs deterministic hard gates via the Python evaluator (supports both csv_writer and triggered_task exercises), performs AI judgment on each one whose hard gates passed, and produces an aggregated Markdown report at grades/<student>/report.md.
---

# /grade — Skill workflow

You (Claude) are the AI judge. A Python orchestrator handles every deterministic step (project lookup, pipeline name match, hard gates, report rendering). Your job is to judge the tasks whose hard gates passed and to fill in two short prose sections at the end.

This skill supports two modes:

- **Full grading** (default): evaluate every registered exercise, write a fresh `grades/<student>/report.md`, and ask you to fill in the `## Overall` paragraph.
- **Single-task grading** (`--task <slug>`): evaluate only one exercise. The Python `report` step updates just that task's section in the existing report.md in place — the header, counts, date, and `## Overall` are left untouched. If no report exists yet, a minimal single-task one is created (no `## Overall` placeholder). You do NOT write an Overall paragraph in single-task mode.

## Steps

### 1. Plan

Parse `<student>`, optional `--space <project_space>`, and optional `--task <slug>` from the invocation. Then run:

```
.venv/Scripts/python.exe -m evaluator.grade plan "<student>" [--space "<project_space>"] [--task "<slug>"]
```

When `--task` is supplied, the manifest will contain exactly one entry (the target slug). Validate that the slug matches a folder under `exercises/` before invoking — `plan` will return exit 2 with a "No exercise folder named …" error if not.

This writes `.tmp/grades/<student>/manifest.json` listing each task with one of:
- `status: "ready_for_ai"` → hard gates passed; you must judge it (step 2).
- `status: "fail"` → hard gate failed; per-task `evaluation.json` is already complete.
- `status: "missing"` → no matching student pipeline; nothing to judge.
- `status: "needs_prep"` → the exercise's solution cache is missing or stale, or the folder has no `task.json`. Do NOT try to repair it from `/grade`. Surface the reason and tell the user to run `/prep` first, then re-run `/grade`. The manifest still includes these entries so the final report lists them.
- `status: "config_error"` → surface the reason to the user and stop.

Exit code ≠ 0 from `plan` means a setup problem (missing `.env`, project not found, no exercise folders) — surface stderr to the user and stop the whole run.

### 2. Judge each `ready_for_ai` task

For every manifest entry with `status: "ready_for_ai"`, read its `ai_context_path` and write the verdict to its `evaluation_path` using `json.dumps(..., indent=2)`.

`ai_context.json` always contains: `task_slug`, `task_type` (`csv_writer` or `triggered_task`), `exercise_description`, `general_rules`, `task_notes`, `solution_flow` and `student_flow` (topologically-sorted snap labels — use these for snap-order reasoning; never iterate `snap_map`), `solution_definition`, `student_definition`, `student_version_notes` (list of `{version_number, creator, time_created, version_tag, version_note}` from the Designer "Versions" dialog; empty list if the student never created a checkpoint), `hard_gates`.

When `task_type == "triggered_task"`, the bundle also contains:
- `triggered_task_name_expected` — the convention name (`<pipeline name> Task`). The hard gate already verified a task with this exact name exists in the student's project; you do not need to re-judge naming.
- `triggered_task_scenarios` — list of `{name, params, expected, student, student_http_status, student_error}` per scenario. `expected` and `student` are parsed JSON (or raw text if invalid). The hard gate already verified every scenario response structurally matches; these are included so you can reason about *how* the student's pipeline produced them when judging structure / bad practice.

**Judging principles** (apply in order):

1. There is usually more than one correct way to solve an exercise. Do not penalize stylistic choices, naming, or differently-shaped snaps that achieve the same correct outcome.
2. Penalize only meaningful problems: incorrect logic, real bad practice (performance, correctness, maintainability), or explicit violations of `task_notes` / `exercise_description`.
3. Reason about snap order from `solution_flow` / `student_flow`, not `snap_map`.
4. Be specific. Name the snaps involved when flagging a difference and explain why it matters or doesn't.
5. If the exercise has a bonus question, look for the student's answer in this priority order — students put it in different places: (a) **`student_version_notes[*].version_note`** — the per-checkpoint comments from the Designer "Versions" dialog, the canonical place; (b) pipeline-level `property_map.info.notes`, `property_map.info.purpose`, `property_map.info.pipeline_doc_uri`; (c) sticky notes in `render_map.notes`; (d) snap-level `property_map.info.notes` / `info.purpose` inside any snap in `snap_map`. When reporting a "not answered" finding, **name the specific fields you checked** (e.g. *"no answer in version notes, info.notes, info.purpose, sticky notes, or any snap-level notes"*); never assert absence on the basis of one field alone. Summarise the found answer + assessment in `bonus_question_answer`, or set it to null if genuinely missing across all those fields.

**Required JSON shape** for `evaluation.json` (downstream code reads it — don't deviate):

```json
{
  "verdict": "pass" | "pass_with_minor_issues" | "fail",
  "summary": "2–3 sentence overview",
  "differences": [
    {
      "area": "snap order | snap config | pipeline parameters | bad practice | ...",
      "severity": "major" | "minor" | "cosmetic",
      "description": "what specifically differs",
      "matters": true | false,
      "reasoning": "why this matters, or why it's fine"
    }
  ],
  "bonus_question_answer": "summary + assessment, or null",
  "failing_gate": null,
  "failing_gate_detail": null
}
```

### 3. Render report

Run (pass the same `--task` you passed to `plan`, if any):

```
.venv/Scripts/python.exe -m evaluator.grade report "<student>" [--space "<project_space>"] [--task "<slug>"]
```

**Full mode** (no `--task`): writes `grades/<student>/report.md` (the persistent location, outside `.tmp/`) with all per-task sections rendered from the per-task `evaluation.json` files, then deletes `.tmp/grades/<student>/` — only the report.md survives. The report contains one placeholder TODO comment — `## Overall`. Use the `Edit` tool to replace it in `grades/<student>/report.md`:

- `## Overall`: one paragraph summarizing the submission. Flag patterns across tasks (e.g. "consistently swaps filter/sort order").

After step 3 runs in full mode, the per-task `ai_context.json` and `evaluation.json` files are gone. You don't need them — fill in the Overall paragraph from the conversation context you already have.

**Single-task mode** (`--task <slug>`): replaces only that task's `## <slug> — …` section in the existing `grades/<student>/report.md`, leaving the header, counts, date, and `## Overall` untouched. If no report exists yet, a minimal single-task report is written instead (no `## Overall` placeholder). Do NOT write or edit an `## Overall` paragraph in this mode — the existing one (if any) is intentionally preserved, and a single-task re-grade should not claim to have re-evaluated the whole submission. The `.tmp/grades/<student>/` scratch dir is still cleaned up after.

### 4. Tell the user

Print to chat:
- One line per task: `<slug> → <verdict>` (the `report` subcommand already prints this — relay it). In single-task mode this is one line.
- The report path: `grades/<student>/report.md`.
- One sentence of overall guidance. In single-task mode, mention that only that one section was updated and the rest of the report (including `## Overall`) is unchanged.

## Notes

- If `plan` reports an ambiguous fuzzy name match for a task (multiple plausible pipelines), call this out in the `## Overall` section since name match is the most basic expectation.
- Never modify anything under `evaluator/`, `exercises/`, or `.claude/context/`. If you'd want to, surface it as a recommendation to the user instead.
