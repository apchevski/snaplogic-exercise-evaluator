# Task 04 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is —
do not restate them here.

## Output matching (task-specific — read first)

This exercise pulls **random users on every run** from a public API
(https://randomuser.me/api/), so the student's output will **never**
match the solution's `Born_on_Friday.xlsx` row-for-row. The output hard
gate is therefore configured in **`columns_only`** mode for this task
(`output_match_mode` in `task.json`): it compares only the **column
header**, not the row data. A correct submission whose columns match
**passes** the gate and is graded normally — do **not** treat differing
row data as a failure, and do **not** deduct for it.

What **must** match are the **columns** — the same column names (the gate
enforces this; column order does not matter), plus the per-field
formatting — not the row data:

- Full Name
- City
- Country
- DOB — formatted `dd-MM-yyyy`
- Age
- Day Of Week — always `Friday`

If the columns are present, correctly named, and correctly derived
(age ≥ 40 **and** born on a Friday; DOB reformatted to `dd-MM-yyyy`),
the output is correct regardless of which random users came back.

## Things that matter (task-specific)

- **Request the 100 users via the `results=100` query parameter, not a
  Sequence snap. `-3 points`.**
  All 100 random users should be retrieved in a single GET using the
  `?results=100` query parameter on the API call. Starting the pipeline
  with a **Sequence snap** (or any equivalent looping construct) to make
  100 single-user calls instead is the wrong approach and signals the
  student did not read the API documentation. Deduct **`-3`** when a
  Sequence snap is used in place of the `results=100` query parameter.

- **Trim the response with the `inc=name,location,dob` query parameter.
  `-2 points`.**
  The API supports `?inc=name,location,dob` to return only the fields the
  exercise needs. Pulling the full user body and picking the fields out of
  it inside the Mapper still works, but skips the documented `inc`
  parameter and fetches far more data than necessary. Deduct **`-2`** when
  the `inc` query parameter is not used.

  *Rationale for both query-parameter rules:* using them is the point of
  the exercise. A student who skips them did not analyze the API
  documentation, so these are the key mistakes to watch for here.

- **Filter placement: Split first, then Filter — the Filter must come
  before the Mapper. `-1 point`.**
  A **Split** (JSON Splitter) snap after the GET is required so the
  pipeline works on each user individually instead of on the whole list —
  this is expected and correct. The row-reducing **Filter** (age ≥ 40 and
  born on Friday) should sit right after the Split, so the Mapper only
  shapes the rows that survive. If the Filter is placed **after the Mapper**
  (or any similar transform), deduct **`-1`** — it formats rows that are
  about to be discarded.

## Things that don't matter (task-specific)

- The specific random users returned, and therefore the exact row content
  and row count of the output file. Only the column set and the per-field
  logic matter (see "Output matching" above).
- Different but equivalent expressions for the same logical check (e.g.
  different correct ways to test "born on a Friday" or to reformat the DOB
  to `dd-MM-yyyy`).
