# Task 01 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is —
do not restate them here.

## Things that matter (task-specific)

> The "zip inputs must use a ZipFile Read snap (`-5 points`)" rule is
> now in `general_evaluation_rules.md` as a universal rule. It applies
> here automatically — don't restate it.

- **Output filter: California residents only. `-2 points`.**
  The pipeline must keep only rows where the state is California.
  Already caught by the CSV hard gate, but if the filter expression is
  visibly wrong in a way that *happens* to produce the right rows on
  this dataset (unlikely), deduct **`-2`**.

- **Sort order: last name, descending. `-2 points`.**
  If the student sorts ascending, or sorts by the wrong column, deduct
  **`-2`** (this should also already be caught by the CSV output hard
  gate before the AI runs — if the gate already failed, that's the
  governing FAIL).

## Bonus question

The exercise asks why the preview shows far fewer than 4000 records.
Expected answer: SnapLogic pipeline previews are limited to a default
sample size per snap (50 rows by default). Any answer that conveys
this idea should be accepted.

Placement of the answer (version notes vs. sticky note vs. missing) is
governed by the universal rule in `general_evaluation_rules.md`.
