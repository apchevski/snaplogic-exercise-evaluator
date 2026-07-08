---
name: grade-points-system
description: /grade resolves every exercise to PASS / FAIL / MISSING and assigns 0-10 points (or null for MISSING, which counts as 0/10 in the per-student total). FAIL splits into procedural (0 pts, no AI) and output-mismatch (AI judges for partial credit). The denominator of the per-student total is always (total exercises) × 10. Point deductions are written into the rule files (general_evaluation_rules.md + per-exercise notes.md). The AI judge never invents a deduction value.
scope: skill:grade
---

# Grading uses verdicts + points, with deductions written into the rules

`/grade` produces exactly three verdicts. The previous
`pass_with_minor_issues` verdict no longer exists.

| Verdict     | When                                                                  | Points |
|-------------|-----------------------------------------------------------------------|--------|
| **PASS**    | Every hard gate passed                                                | `10 − Σ deductions`, floor `0`. Verdict stays PASS even if deductions exceed 10. |
| **FAIL** (output-mismatch) | `csv_output_match` or `triggered_task_responses_match` failed — output is wrong | `10 − Σ deductions`, floor `0` — AI judges pipeline structure for partial credit |
| **FAIL** (procedural)      | Pipeline name wrong                                                   | `0`, AI not invoked |
| **MISSING** | No matching pipeline, OR no output uploaded to SLDB (`csv_output_present` 404), OR no Triggered Task with the convention name (`triggered_task_exists`) | `null` per-task (not graded), but contributes `0/10` to the per-student total |

### Why FAIL has two flavors

A student whose pipeline is structurally correct except for one
misspelled string literal should not be ranked alongside a student
who submitted an empty pipeline. So output-mismatch FAILs (the
"single word differs" case) still go to the AI for partial credit —
the verdict stays FAIL because the output is wrong, but points
reflect how close the pipeline is to a correct solution.

Procedural FAILs are different: if the pipeline isn't named correctly,
there's nothing partial to credit and no point in spending AI tokens.

"Student didn't submit a runnable deliverable" is **MISSING**, not
FAIL. Three flavors today:

- No pipeline matching the solution name at all.
- (csv_writer) Pipeline exists but the expected CSV isn't in SLDB
  — the student never ran it.
- (triggered_task) Pipeline exists but no Triggered Task with the
  convention name `<pipeline name> Task` — the student didn't
  create the deliverable that lets the task be invoked.

In all three, the submission can't be AI-judged (there's nothing to
judge — the deliverable isn't there), so the per-task row shows
`Points: —/10`. But the per-student **total** denominator is always
`(total exercises) × 10`, regardless of how many were actually
graded. A MISSING exercise contributes `0` to the numerator and `10`
to the denominator — a student who skips half the exercises sees
that reflected in their total, instead of having the missing ones
silently dropped.

### Routing

`evaluator/evaluate.py` decides which path each gate failure takes:

- **Output-mismatch gates** (`_OUTPUT_MISMATCH_GATES`) write
  `ai_context.json` (with the failure recorded in `hard_gates`) and
  exit 0; the AI judge reads `hard_gates`, emits `verdict: "fail"`,
  and applies the same rule-based deductions it would for a PASS.
- **Procedural gates** (pipeline name) write a complete
  `evaluation.json` with `verdict: "fail"`, `points: 0` and exit 1.
- **"Deliverable not submitted" gates** (`csv_output_present`,
  `triggered_task_exists`) write a `verdict: "missing"` artifact
  (`points: null`) and exit 4; `evaluator/grade.py:cmd_plan` reads
  the exit code and adds the manifest entry with
  `status: "missing"`.

The AI never has to decide *whether* it should judge — if a bundle
exists, it judges. It does have to read `hard_gates` to decide the
verdict (pass vs fail).

## Deductions are stated in the rules, not invented by the model

Each soft rule that can cost points states its value (`-2 points`,
`-1 point`, or *mention only*) explicitly. Two places:

- **`exercises/general_evaluation_rules.md`** — universal rules
  applied to every submission (filter-before-sort, CSV Formatter
  options, Mapper Pass-through, etc.).
- **`exercises/<slug>/notes.md`** — per-exercise rules. May extend the
  universal list with task-specific deductions or override a universal
  rule.

The `/grade` SKILL.md tells the AI: **use the rule's value verbatim;
never invent a deduction.** If the AI notices something off that has
no governing rule with explicit points, it becomes a **Note** in the
report (`points_deducted: 0`) — surfaced to the student, but no points
are deducted.

## Consistency across students

The reason deductions live in the rules (not in the AI's head) is to
guarantee that the same mistake costs the same points for every
student, every time. If you want to change what a mistake is worth,
edit the rule — don't try to argue with the model.

If you encounter a new category of issue that recurs across submissions
and feels worth deducting, add it to `general_evaluation_rules.md` (or
the relevant `notes.md`) with an explicit point value, **then** re-run
`/grade`. Future grades will apply the new rule consistently.

## `evaluation.json` shape (downstream contract)

```json
{
  "verdict": "pass",        // "pass" | "fail" — AI reads hard_gates to decide
  "points": 8,              // == max(0, 10 - sum(points_deducted))
  "summary": "...",
  "differences": [
    {
      "area": "...",
      "description": "...",
      "points_deducted": 2, // 0 | 1 | 2 | 5 — literal from the rule
      "rule_source": "general_rules: filter before sort",
      "reasoning": "..."
    }
  ],
  "bonus_question_answer": "...",
  "failing_gate": null,        // populate with the failing gate name on output-mismatch FAIL
  "failing_gate_detail": null  // populate with the gate's detail string on output-mismatch FAIL
}
```

The renderer (`evaluator/grade.py`) splits `differences` into
**Deductions** (`points_deducted > 0`) and **Notes** (`points_deducted == 0`)
when writing per-task sections. The report header shows the per-student
total as `points_earned / points_possible` where `points_possible` is
always `(total exercises) × 10` — MISSING / NEEDS_SYNC exercises
contribute `0` to the numerator but still count in the denominator.
