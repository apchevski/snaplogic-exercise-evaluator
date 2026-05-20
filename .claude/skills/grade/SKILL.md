---
name: grade
description: Grade a SnapLogic student's exercises by comparing each of their pipelines against the official solution. Usage — /grade <student name>  OR  /grade --space <project space> <student name>. Iterates every exercise registered in exercises/<slug>/task.json, runs deterministic hard gates via the Python evaluator, performs AI judgment on each one whose hard gates passed, and produces an aggregated Markdown report at grades/<student>/report.md.
---

# /grade — Skill workflow

You (Claude) are the AI judge. A Python orchestrator handles every deterministic step (project lookup, pipeline name match, hard gates, report rendering). Your job is to judge the tasks whose hard gates passed and to fill in two short prose sections at the end.

## Steps

### 1. Plan

Parse `<student>` and optional `--space <project_space>` from the invocation. Then run:

```
.venv/Scripts/python.exe -m evaluator.grade plan "<student>" [--space "<project_space>"]
```

This writes `.tmp/grades/<student>/manifest.json` listing each task with one of:
- `status: "ready_for_ai"` → hard gates passed; you must judge it (step 2).
- `status: "fail"` → hard gate failed; per-task `evaluation.json` is already complete.
- `status: "missing"` → no matching student pipeline; nothing to judge.
- `status: "needs_prep"` → the exercise's solution cache is missing or stale, or the folder has no `task.json`. Do NOT try to repair it from `/grade`. Surface the reason and tell the user to run `/prep` first, then re-run `/grade`. The manifest still includes these entries so the final report lists them.
- `status: "config_error"` → surface the reason to the user and stop.

Exit code ≠ 0 from `plan` means a setup problem (missing `.env`, project not found, no exercise folders) — surface stderr to the user and stop the whole run.

### 2. Judge each `ready_for_ai` task

For every manifest entry with `status: "ready_for_ai"`, read its `ai_context_path` and write the verdict to its `evaluation_path` using `json.dumps(..., indent=2)`.

`ai_context.json` contains: `exercise_description`, `general_rules`, `task_notes`, `solution_flow` and `student_flow` (topologically-sorted snap labels — use these for snap-order reasoning; never iterate `snap_map`), `solution_definition`, `student_definition`, `student_version_notes` (list of `{version_number, creator, time_created, version_tag, version_note}` from the Designer "Versions" dialog; empty list if the student never created a checkpoint), `hard_gates`.

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

Run:

```
.venv/Scripts/python.exe -m evaluator.grade report "<student>" [--space "<project_space>"]
```

This writes `grades/<student>/report.md` (the persistent location, outside `.tmp/`) with all per-task sections rendered from the per-task `evaluation.json` files, then deletes `.tmp/grades/<student>/` — only the report.md survives. The report contains one placeholder TODO comment — `## Overall`. Use the `Edit` tool to replace it in `grades/<student>/report.md`:

- `## Overall`: one paragraph summarizing the submission. Flag patterns across tasks (e.g. "consistently swaps filter/sort order").

After step 3 runs, the per-task `ai_context.json` and `evaluation.json` files are gone. You don't need them — fill in the Overall paragraph from the conversation context you already have.

### 4. Tell the user

Print to chat:
- One line per task: `<slug> → <verdict>` (the `report` subcommand already prints this — relay it).
- The report path: `grades/<student>/report.md`.
- One sentence of overall guidance.

## Notes

- If `plan` reports an ambiguous fuzzy name match for a task (multiple plausible pipelines), call this out in the `## Overall` section since name match is the most basic expectation.
- Never modify anything under `evaluator/`, `exercises/`, or `.claude/context/`. If you'd want to, surface it as a recommendation to the user instead.
