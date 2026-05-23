# Task 01 Bonus 1 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

This exercise builds directly on **Task 01**. The student copies their
Task 01 pipeline into a new pipeline and adds a **birth-year filter**:
the output should contain only California residents born in **1963,
1964, or 1965**. The universal best-practice rules in
`general_evaluation_rules.md` and all Task 01 task-specific rules
still apply — only the points below are specific to this bonus.

## Things that matter (new for Bonus 1)

- **The pipeline must filter to birth years 1963, 1964, and 1965.
  `-2 points`.** This is the core requirement of the bonus. If the
  year filter is missing, uses the wrong years, or uses the wrong
  inclusivity (e.g. 1964–1965 only, or 1962–1965), deduct **`-2`**.
  The CSV output hard gate should already catch most miscounts as a
  FAIL — this deduction applies when the filter is visibly wrong but
  the output still matches.

- **The year check must be combined with the California check in a
  single Filter snap. `-1 point`.** The combined expression is short
  and readable (e.g. `$State == "CA" && [1963,1964,1965].contains($DOB.year)`),
  so there is no need to split the state and year checks across two
  Filter snaps. Any equivalent single-snap expression
  (`$DOB.year >= 1963 && $DOB.year <= 1965`, regex on the DOB string,
  etc.) is acceptable as long as the result is correct. If the
  student uses two separate Filter snaps instead of one combined
  snap, deduct **`-1`**.

## Things that don't matter (new for Bonus 1)

- The exact form of the year expression, as long as it lives in the
  single combined Filter snap and produces the correct result.
