# Task 02 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is.

Unlike Task 01, this exercise does not produce a CSV. The pipeline is
exposed as a **Triggered Task** and the evaluator verifies behavior by
calling that task over HTTP with different `mathOperation` values and
comparing the JSON response. The Triggered Task name (`<pipeline name>
Task`) and per-scenario response matching are both enforced by hard
gates before the AI evaluator runs (see `general_evaluation_rules.md`,
rules 3 and 4).

The evaluator issues five GET requests covering the four supported
operators (`+`, `-`, `*`, `/`) plus one input with no valid operator.
Each response must match the expected shape exactly:
- With an operator: `[{"result": "3 + 5 = 8"}]`
- Without an operator: `[{"result": "No operator in the equation"}]`

## Things that matter (task-specific)

- **The "no operator" branch must be implemented. `-2 points`.**
  The pipeline must explicitly handle the case where the input
  contains no valid operator and return the canonical
  `"No operator in the equation"` response. If this branch is
  missing (e.g. the pipeline errors out, returns an empty array, or
  returns a partial calculation), deduct **`-2`** (this case is
  exercised by the no-operator scenario in the hard gates, so a
  missing branch will usually have already produced a FAIL).

- **Prefer Mapper → Conditional → Mapper (3 snaps) over
  Router + 5 Mappers + Union. `-1 point`.**
  The clean solution uses a single Conditional snap to dispatch on
  the operator and a single downstream Mapper to shape the result.
  An alternative implementation using a Router with five outputs,
  five per-operator Mapper snaps, and a Union to recombine them is
  functionally equivalent but unnecessarily wide and harder to
  maintain. If the student used the Router-fan-out approach, deduct
  **`-1`** — the result is correct, but the Conditional-based
  layout is the preferred pattern.

## Things that don't matter (task-specific)

- **The exact expression used to parse the operator or perform the
  calculation.** Any expression that correctly extracts the operator
  from `$mathOperation` and produces the right arithmetic result is
  acceptable — regex, `indexOf` + `substring`, `split`, `match`, etc.
  Do not penalize stylistic differences here as long as all five
  evaluator scenarios pass.

- **The exact format of the equation echo in the result string**, as
  long as the final value matches the expected response byte-for-byte
  (e.g. spacing around the operator must be `3 + 5 = 8`, not
  `3+5=8`). This is enforced by the response comparison, not by
  inspecting the expression.
