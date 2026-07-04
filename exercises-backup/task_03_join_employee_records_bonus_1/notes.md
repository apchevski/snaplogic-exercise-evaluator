# Task 03 Bonus 1 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

This exercise builds directly on **Task 03**. The student copies their
Task 03 pipeline into a new pipeline and adds a derived column named
**`MoreThan10K-Boolean`** whose value is `true` when `$MoreThan10K`
is greater than `0.43` and `false` otherwise. The universal
best-practice rules in `general_evaluation_rules.md` and all Task 03
task-specific rules still apply — only the points below are specific
to this bonus.

## Things that matter (new for Bonus 1)

- **Output must contain a `MoreThan10K-Boolean` column. `-2 points`.**
  Already enforced by the CSV output hard gate as a FAIL, but if the
  output happens to match without the column being produced
  (unlikely), deduct **`-2`**.

- **The value must be a real Boolean, not a string. `-2 points`.**
  The exercise explicitly calls this out. The expression must yield
  `true` / `false` as Boolean values (e.g.
  `$MoreThan10K > 0.43`), not the strings `"true"` / `"false"` from
  a ternary like `$MoreThan10K > 0.43 ? "true" : "false"`. If the
  student produces string values that happen to read as `true`/`false`,
  deduct **`-2`** — the requirement is type-specific, not just
  text-equivalent.

- **Threshold must be strictly greater than 0.43, and the
  equal-to-0.43 case must yield `null`.**
  Per the exercise: values *more than* 0.43 are `true`, values *less
  than* 0.43 are `false`. The description does not cover the
  equality case, which means the correct behavior is to return
  `null` (neither `true` nor `false`) when `$MoreThan10K` is exactly
  `0.43`. A simple two-branch ternary like
  `$MoreThan10K > 0.43 ? true : false` is **incorrect** because it
  silently buckets `0.43` rows as `false`. A correct expression is
  three-way, e.g.
  `$MoreThan10K == 0.43 ? null : $MoreThan10K > 0.43`
  or any equivalent form that explicitly produces `null` on
  equality.
  - Deduct **`-2`** if the comparator or threshold value itself is
    wrong (e.g. `>=` instead of `>`, or wrong number).
  - Deduct **`-1`** if the comparator is right but the expression
    does not handle the equality case as `null` — the visible CSV
    output may not differ if the dataset contains no `0.43` rows,
    but the expression is still incorrect by spec and shows the
    student did not think about edge cases.

- **The new field must be added to the existing Mapper carried over
  from Task 03. `-1 point`.** The Task 03 pipeline already has a
  Mapper shaping the output rows; the `MoreThan10K-Boolean` field
  belongs as another mapping inside that same snap. (This is a
  task-specific application of the universal "no extra Mapper snaps"
  and "Mapper Pass through" rules — apply this rule once, not both.)
  Deduct **`-1`** if the student introduced a separate Mapper for
  the new column instead of extending the existing one.

## Things that don't matter (new for Bonus 1)

- The exact form of the boolean expression, as long as it yields a
  real Boolean and uses the correct `> 0.43` threshold.
