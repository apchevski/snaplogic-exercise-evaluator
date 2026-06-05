---
name: grade
description: Grade a SnapLogic student's exercises by comparing each of their pipelines against the official solution. Usage — /grade <student name>  OR  /grade --space <project space> <student name>  OR  /grade <student name> --task <slug> (grade only one exercise, updating that task's section in the existing report in place). Iterates every exercise registered in exercises/<slug>/task.json, runs deterministic hard gates via the Python evaluator (supports both file_writer and triggered_task exercises), performs AI judgment on each one whose hard gates passed, and produces an aggregated Markdown report at grades/<student>/report.md.
---

# /grade — Skill workflow

You (Claude) are the AI judge. A Python orchestrator handles every deterministic step (project lookup, pipeline name match, hard gates, report rendering). Your job is to judge the tasks whose hard gates passed and to fill in two short prose sections at the end.

This skill supports two modes:

- **Full grading** (default): evaluate every registered exercise, write a fresh `grades/<student>/report.md`, and ask you to fill in the `## Overall` paragraph.
- **Single-task grading** (`--task <slug>`): evaluate only one exercise. The Python `report` step updates just that task's section in the existing report.md in place (the date is left untouched). Afterwards you **refresh the `## Overall` paragraph and reconcile the header counts/total** so the report reflects the just-(re-)graded task — every grading run leaves a current Overall, never a stale one. If no report exists yet, a minimal single-task one is created (no `## Overall` placeholder — nothing to refresh).

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
- `status: "missing"` → the student didn't submit a runnable deliverable. Three flavors: (a) no matching student pipeline, (b) pipeline exists but **none** of the expected output file(s) are in SLDB (`output_present` 404, file_writer only — for a multi-output exercise this means every file is absent, i.e. the pipeline was never run), or (c) pipeline exists but no Triggered Task with the convention name (`triggered_task_exists` failed, triggered_task only). All three are excluded from totals; nothing to judge.
- `status: "needs_prep"` → the exercise's solution cache is missing or stale, or the folder has no `task.json`. Do NOT try to repair it from `/grade`. Surface the reason and tell the user to run `/prep` first, then re-run `/grade`. The manifest still includes these entries so the final report lists them.
- `status: "config_error"` → surface the reason to the user and stop.

Exit code ≠ 0 from `plan` means a setup problem (missing `.env`, project not found, no exercise folders) — surface stderr to the user and stop the whole run.

### 2. Judge each `ready_for_ai` task

For every manifest entry with `status: "ready_for_ai"`, read its `ai_context_path` and write the verdict to its `evaluation_path` using `json.dumps(..., indent=2)`.

`ai_context.json` always contains: `task_slug`, `task_type` (`file_writer` or `triggered_task`), `exercise_description`, `general_rules`, `task_notes`, `solution_flow` and `student_flow` (topologically-sorted snap labels — use these for snap-order reasoning; never iterate `snap_map`), `solution_definition`, `student_definition`, `student_version_notes` (list of `{version_number, creator, time_created, version_tag, version_note}` from the Designer "Versions" dialog; empty list if the student never created a checkpoint), `hard_gates`.

When `task_type == "triggered_task"`, the bundle also contains:
- `triggered_task_name_expected` — the convention name (`<pipeline name> Task`). The hard gate already verified a task with this exact name exists in the student's project; you do not need to re-judge naming.
- `triggered_task_scenarios` — list of `{name, params, expected, student, student_http_status, student_error}` per scenario. `expected` and `student` are parsed JSON (or raw text if invalid). If the responses-match hard gate passed, these are FYI; if it failed (see below), they tell you *which* scenarios diverged.

#### Decide the verdict from `hard_gates`

Before you start deducting, read the `hard_gates` array in `ai_context.json`:

- **All hard gates passed** → emit `verdict: "pass"`. Points start at 10, minus any rule-based deductions.
- **An output-mismatch gate failed** (`output_match` or `triggered_task_responses_match`) → emit `verdict: "fail"`. The orchestrator routed this case to you specifically so you can award partial points for pipeline structure even though the output is wrong. Points still start at 10 and you still deduct using the same rules — the output mismatch itself is **not** a separate deduction (FAIL already conveys "output is wrong"). Don't double-penalize. For a **multi-output** file_writer exercise, `output_match` aggregates every expected file into one gate; its detail lists each file's PASS/FAIL (and which rows/columns differed), so use it to see which report(s) diverged. It's still one gate and one FAIL — don't add an extra deduction per differing or missing file.
- **Any other gate failed** — you will never see this case. Procedural FAILs (pipeline name wrong) are handled by the orchestrator with a fixed 0-point FAIL artifact; no AI context bundle is written. "Deliverable not submitted" gates (`output_present` 404, `triggered_task_exists` missing) are handled by the orchestrator as MISSING (not graded, excluded from totals); also no AI context bundle. If you somehow see one of these in a bundle, emit `verdict: "fail"` with `points: 0`.

You never emit `verdict: "missing"` — MISSING is the orchestrator's status and no bundle is written.

**Judging principles** (apply in order):

1. **Points start at 10. Deduct only using values explicitly written in `general_rules` or `task_notes`.** Each rule that can cost points states its value (e.g. `-5 points`, `-2 points`, `-1 point`, or *mention only*). Use that exact value. **Never invent a deduction value.** If you see an issue that has no governing rule with explicit points, it becomes a Note (`points_deducted: 0`) — surface it to the student, but do not deduct.
2. **Same rule, one deduction per exercise.** If the student violates the same rule in two places within one exercise (e.g. two default-named snaps), deduct the rule's value **once**. Name all occurrences in the description.
3. **Floor at 0; verdict is independent of points.** If deductions sum past 10, `points` is `0`, never negative. The verdict stays whatever the hard gates decided: PASS stays PASS at 0 points (output is right), FAIL stays FAIL at 0 points (output is wrong). Points and verdict are two separate signals.
4. **On FAIL, when a rule is already obviously the cause of the output mismatch, still deduct it.** The most common case: the output differs because the student's filter/sort is wrong, and that *same* configuration also violates a soft rule. The deduction still applies — the rule is what makes the difference *visible* and *consistent across students*. Don't add an *extra* "your output was wrong" deduction on top — FAIL already says that.
5. There is usually more than one correct way to solve an exercise. Do not penalize stylistic choices, naming, or differently-shaped snaps that achieve the same correct outcome.
6. Reason about snap order from `solution_flow` / `student_flow`, not `snap_map`.
7. Be specific. Name the snaps involved when flagging a difference and explain why it matters or doesn't.
8. If the exercise has a bonus question, look for the student's answer in this priority order — students put it in different places: (a) **`student_version_notes[*].version_note`** — the per-checkpoint comments from the Designer "Versions" dialog, the canonical place; (b) pipeline-level `property_map.info.notes`, `property_map.info.purpose`, `property_map.info.pipeline_doc_uri`; (c) sticky notes in `render_map.notes`; (d) snap-level `property_map.info.notes` / `info.purpose` inside any snap in `snap_map`. When reporting a "not answered" finding, **name the specific fields you checked** (e.g. *"no answer in version notes, info.notes, info.purpose, sticky notes, or any snap-level notes"*); never assert absence on the basis of one field alone. Summarise the found answer + assessment in `bonus_question_answer`, or set it to null if genuinely missing across all those fields. Apply the bonus-placement rule from `general_rules`: **correct answer in any of those fields → no deduction** (mention placement under Notes if it's outside version notes), **answer missing or wrong → `-2`**.

**Required JSON shape** for `evaluation.json` (downstream code reads it — don't deviate):

```json
{
  "verdict": "pass",
  "points": 8,
  "summary": "2–3 sentence overview",
  "differences": [
    {
      "area": "snap order | snap config | pipeline parameters | bad practice | ...",
      "description": "what specifically differs (name the snaps)",
      "points_deducted": 2,
      "rule_source": "general_rules: filter before sort",
      "reasoning": "why this matters per the rule"
    },
    {
      "area": "naming",
      "description": "snap label 'Filter1' is fine but could be more descriptive",
      "points_deducted": 0,
      "rule_source": null,
      "reasoning": "no governing rule with explicit points → mention only"
    }
  ],
  "bonus_question_answer": "summary + assessment, or null",
  "failing_gate": null,
  "failing_gate_detail": null
}
```

When you emit `verdict: "fail"` (output-mismatch FAIL routed to you), also populate `failing_gate` and `failing_gate_detail` from the failing entry in `hard_gates`, so the renderer can show the student which gate caused the FAIL alongside the partial-credit pipeline review.

Where:
- `points` MUST equal `max(0, 10 - sum(points_deducted))`. The renderer trusts this value — compute it correctly.
- `points_deducted` is an integer (typically `0`, `1`, `2`, or `5`); use the literal value the rule states. `0` means the issue is a Note (surfaced to the student) and is not deducted.
- `rule_source` is a short hint like `"general_rules: filter before sort"` or `"task_notes: no operator branch"` so the renderer/reader can trace each deduction back. Use `null` for Notes (no rule).

### 3. Render report

Run (pass the same `--task` you passed to `plan`, if any):

```
.venv/Scripts/python.exe -m evaluator.grade report "<student>" [--space "<project_space>"] [--task "<slug>"]
```

Both modes silently rebuild `ui/index.html` after writing the report so the dashboard reflects the latest grades — you do NOT need to run `python -m evaluator.ui` yourself.

**Full mode** (no `--task`): writes `grades/<student>/report.md` (the persistent location, outside `.tmp/`) with all per-task sections rendered from the per-task `evaluation.json` files, plus a structured mirror at `grades/<student>/report.json` for downstream tooling (future UI). Then deletes `.tmp/grades/<student>/` — only the persistent files survive. The report contains one placeholder TODO comment — `## Overall`. Use the `Edit` tool to replace it in `grades/<student>/report.md`:

- `## Overall`: one paragraph summarizing the submission. Flag patterns across tasks (e.g. "consistently swaps filter/sort order").

After editing the Overall paragraph into report.md, run:

```
.venv/Scripts/python.exe -m evaluator.grade sync-overall "<student>"
```

This copies the paragraph you wrote into `overall_summary` in `report.json` so the JSON mirror stays in sync with the markdown, then rebuilds `ui/index.html` so the dashboard picks up the new Overall summary. Always run it after editing `## Overall` in full mode.

After step 3 runs in full mode, the per-task `ai_context.json` and `evaluation.json` files are gone. You don't need them — fill in the Overall paragraph from the conversation context you already have.

**Single-task mode** (`--task <slug>`): replaces only that task's `## <slug> — …` section in the existing `grades/<student>/report.md` (the date is left untouched). The matching task entry in `grades/<student>/report.json` is updated in lockstep, and `report.json`'s `counts` / `points_earned` / `points_possible` are recomputed from the merged task list — **but the markdown header is NOT** (the Python leaves the `Exercises evaluated`, `Pass`/`Fail`/`Missing`, and `Total` lines stale, so they go out of date the moment a new task is added or a re-grade changes a score). So after the `report` command, do three things:

1. **Reconcile the markdown header.** Read `grades/<student>/report.json` and Edit the `- **Exercises evaluated**`, `- **Pass**: … · **Fail**: … · …`, and `- **Total**: …/… points` lines in report.md to match `counts` / `points_earned` / `points_possible`.
2. **Refresh `## Overall`.** Edit the existing `## Overall` paragraph so it reads as one coherent summary of the *whole current* submission, folding in the task you just (re-)graded and quoting the corrected total — don't merely append a sentence. (This is the one place single-task mode deviates from "touch only the one section": a stale Overall that contradicts the new score is worse than re-stating the whole picture.)
3. **Run `sync-overall`** (same command as full mode) to copy the refreshed paragraph into `report.json` and rebuild `ui/index.html`.

If no report exists yet, a minimal single-task report (md + json) is written instead with **no** `## Overall` placeholder — its header already reflects the single task, so skip steps 1–3 entirely. The `.tmp/grades/<student>/` scratch dir is cleaned up after either way.

### 4. Tell the user

Print to chat:
- One line per task: `<slug> → <verdict>` (the `report` subcommand already prints this — relay it). In single-task mode this is one line.
- The report path: `grades/<student>/report.md`.
- One sentence of overall guidance. In single-task mode, mention that the task's section was updated and that the `## Overall` paragraph and header totals were refreshed to include it (the other tasks' sections were not re-evaluated).

## Notes

- If `plan` reports an ambiguous fuzzy name match for a task (multiple plausible pipelines), call this out in the `## Overall` section since name match is the most basic expectation.
- Never modify anything under `evaluator/`, `exercises/`, or `.claude/context/`. If you'd want to, surface it as a recommendation to the user instead.
