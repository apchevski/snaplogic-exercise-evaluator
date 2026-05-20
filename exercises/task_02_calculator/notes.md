# Task 02 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

Unlike Task 01, this exercise does not produce a CSV. The pipeline is
exposed as a **Triggered Task** and the evaluator verifies behavior by
calling that task over HTTP with different `mathOperation` values and
comparing the JSON response.

## Things that matter

- **A Triggered Task with the convention name must exist.**
  The Triggered Task name **must** be `<pipeline name> Task` — for
  this exercise that is exactly `Task 02 – Calculator Task` (pipeline
  name plus the suffix ` Task`). This is enforced by a hard gate
  before the AI evaluator runs: if no Triggered Task with that exact
  name exists in the student's project, the submission is an
  **automatic FAIL**. A correctly-behaving task registered under a
  different name still fails the gate — the convention is strict, not
  a soft preference. The only allowed deviation is the dash glyph:
  hyphen-minus `-`, en dash `–`, and em dash `—` count as the same
  character, so `Task 02 - Calculator Task` also passes.

- **The Triggered Task must return the correct response for every
  tested scenario.**
  The evaluator issues five GET requests covering the four supported
  operators (`+`, `-`, `*`, `/`) plus one input with no valid
  operator. Each response must match the expected shape exactly:
  - With an operator: `[{"result": "3 + 5 = 8"}]`
  - Without an operator: `[{"result": "No operator in the equation"}]`

  Response matching is a hard gate too: any scenario whose response
  doesn't structurally match the cached expected response is an
  **automatic FAIL**. The AI only runs once every scenario matches.

- **The "no operator" branch must be implemented.**
  The pipeline must explicitly handle the case where the input
  contains no valid operator and return the canonical
  `"No operator in the equation"` response. If this branch is
  missing (e.g. the pipeline errors out, returns an empty array, or
  returns a partial calculation), flag as **major**.

- **Prefer Mapper → Conditional → Mapper (3 snaps) over
  Router + 5 Mappers + Union.**
  The clean solution uses a single Conditional snap to dispatch on
  the operator and a single downstream Mapper to shape the result.
  An alternative implementation using a Router with five outputs,
  five per-operator Mapper snaps, and a Union to recombine them is
  functionally equivalent but unnecessarily wide and harder to
  maintain. If the student used the Router-fan-out approach, flag as
  **minor** — the result is correct, but the Conditional-based
  layout is the preferred pattern.

## Things that don't matter

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

- Snap label positions and view-layout coordinates.
