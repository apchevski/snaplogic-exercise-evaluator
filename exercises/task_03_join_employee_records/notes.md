# Task 03 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is —
do not restate them here. In particular, the filter-before-sort rule
extends to **filter before Join** as well: filter the Leads stream
down to San Francisco residents *before* joining against
`CAIncomeByZip.csv`, not after.

## Things that matter (task-specific)

- **The two data sources must be joined on Zip code.**
  The pipeline must join `Leads.csv` with `CAIncomeByZip.csv` so that
  each San Francisco lead is associated with the income distribution
  for their zip code. Any join snap pattern that produces the correct
  output is acceptable (Join snap, in-memory lookup, etc.) — what
  matters is that the join key is Zip code and that the result
  includes the income columns alongside the lead columns. If the join
  is missing, performed on the wrong key, or uses the wrong join type
  such that rows are duplicated or dropped, flag as **major**. (The
  CSV output hard gate will already catch the row-level mismatch —
  name the cause in the pipeline review.)

- **Output filter: San Francisco leads only.**
  Already caught by the CSV hard gate, but if the filter expression is
  visibly wrong in a way that *happens* to produce the right rows on
  this dataset (unlikely), flag it.

- **Sort order: Zip code, ascending.**
  If the student sorts descending, or sorts by the wrong column,
  that's a **major** issue (though it should also already be caught
  by the CSV output hard gate before the AI runs).

## Things that don't matter (task-specific)

- The specific join snap or technique used, as long as the join key
  is Zip code and the resulting rows are correct.

## Bonus question

The exercise asks why the pipeline does not throw an error when a
literal value in the filter expression is misspelled (e.g.
`$City == 'Bitolaa'` instead of `$City == 'Bitola'`).

Expected answer: a misspelled string literal is still a syntactically
valid expression — it simply evaluates to `false` for every record,
so the filter passes zero rows downstream. SnapLogic does not
validate filter literals against the actual data at design time or
runtime, and an empty data stream is a valid (non-error) pipeline
state. The pipeline completes successfully with an empty output. Any
answer that conveys this idea — "the expression is still valid, it
just matches nothing" / "empty result is not an error" — should be
accepted.

Placement of the answer (version notes vs. sticky note vs. missing) is
governed by the universal rule in `general_evaluation_rules.md`.
