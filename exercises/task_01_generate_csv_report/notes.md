# Task 01 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is —
do not restate them here.

## Things that matter (task-specific)

- **The pipeline must unzip Task1.zip via a snap.**
  Manual unzipping is forbidden per the exercise description. If the
  student's pipeline has no ZipFile Read / unzip step and reads the
  uncompressed CSV directly, that is a **major** issue.

- **Output filter: California residents only.**
  The pipeline must keep only rows where the state is California.
  Already caught by the CSV hard gate, but if the filter expression is
  visibly wrong in a way that *happens* to produce the right rows on
  this dataset (unlikely), flag it.

- **Sort order: last name, descending.**
  If the student sorts ascending, or sorts by the wrong column, that's
  a **major** issue (though it should also already be caught by the
  CSV output hard gate before the AI runs).

## Bonus question

The exercise asks why the preview shows far fewer than 4000 records.
Expected answer: SnapLogic pipeline previews are limited to a default
sample size per snap (50 rows by default). Any answer that conveys
this idea should be accepted.

Placement of the answer (version notes vs. sticky note vs. missing) is
governed by the universal rule in `general_evaluation_rules.md`.
