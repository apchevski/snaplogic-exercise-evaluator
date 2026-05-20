# Task 03 Bonus 2 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

This exercise builds directly on **Bonus 1** (which itself builds on
Task 03). The student copies their Bonus 1 pipeline into a new
pipeline and **filters the output down to records where
`MoreThan10K-Boolean` is `true`**. The universal best-practice rules
in `general_evaluation_rules.md` and all Task 03 / Bonus 1
task-specific rules still apply — only the points below are specific
to this bonus.

## Two acceptable solution paths

There are two valid ways to satisfy this bonus, and **both are full
passes**:

1. **Standard path.** Keep the Bonus 1 pipeline intact and add a new
   Filter snap *after* the Bonus 1 Mapper with an expression like
   `$['MoreThan10K-Boolean'] == true`. This is the textbook
   solution: it does exactly what the exercise text describes.

2. **Optimized ("cheeky") path.** Move the row reduction upstream:
   add a Filter snap *before* the Join with the expression
   `$MoreThan10K > 0.43`, which already lets through only the
   records that would have ended up `true` (consistent with the
   strict `> 0.43` threshold from Bonus 1). Then a Mapper after the
   join hardcodes `$MoreThan10K-Boolean = true` on every surviving
   row. This is genuinely better because the Join now operates on
   far fewer documents — the most expensive snap in the pipeline
   gets the smallest possible input.

If the student takes the optimized path, **call it out as exemplary
in the per-task review** ("kudos for moving the filter ahead of the
join — this is the performance-aware solution"). If the student
takes the standard path, that is still a full pass; do not penalize
it.

## Things that matter (new for Bonus 2)

- **A row-reducing step that keeps only the `true`-eligible records
  must be present.** Either pattern is acceptable:
  - Standard: a Filter after the Bonus 1 Mapper with
    `$['MoreThan10K-Boolean'] == true` (or any equivalent
    expression that keeps only true rows).
  - Optimized: a Filter before the Join with
    `$MoreThan10K > 0.43`, paired with a downstream Mapper that
    hardcodes `$MoreThan10K-Boolean = true`.

  If neither pattern is present, the filter keeps the wrong rows, or
  the optimized path is attempted but the post-join Mapper does not
  actually set the boolean column, flag as **major**. The CSV
  output hard gate will already catch the row-level mismatch — name
  the cause in the pipeline review.

- **Sort placement must be optimized for the new pipeline shape.**
  This is a stricter, task-specific extension of the universal
  filter-before-sort rule. Regardless of which solution path is
  used, the Sort/Order snap should sit **after every row-reducing
  step**, as close to the CSV Formatter as possible, so it sorts the
  smallest possible dataset. In the standard path that means moving
  Sort from its Task 03 position to *after* the new boolean filter.
  In the optimized path Sort still belongs at the end of the
  pipeline (downstream of the Join). Leaving Sort upstream of any
  row-reducing filter means sorting rows that will then be thrown
  away. Flag as **major** if Sort is upstream of any row-reducing
  filter, and as **minor** if Sort is in a defensible-but-not-optimal
  position.

- **Column rename (`MoreThan10K-Boolean` → `NewColumn`) is
  acceptable.** The description explicitly tells students they may
  rename the column to `NewColumn` if the dash in
  `MoreThan10K-Boolean` causes filter-expression issues. Either
  column name is acceptable as long as the same field is used
  consistently from the Mapper through to the CSV output. (The CSV
  hard gate's expected output uses whichever name the solution
  pipeline uses — if the student's column name differs, the gate
  will catch the header mismatch; that's fine, just note in the
  pipeline review that the rename was the cause.) This applies
  mainly to the standard path; in the optimized path the boolean
  column is only ever hardcoded to `true` and the rename is
  generally unnecessary.

## Things that don't matter (new for Bonus 2)

- The exact form of the boolean filter expression
  (`$MoreThan10K-Boolean == true`, `$NewColumn`, `!!$NewColumn`,
  etc.), as long as it correctly keeps only `true` rows.
- Whether the student kept the original column name or renamed it to
  `NewColumn` — both are explicitly permitted by the description.
- **Which of the two solution paths the student chose.** Both are
  full passes; the optimized path simply earns explicit praise.

## Bonus question

The exercise asks where to place the Order (Sort) snap now that
Bonus 1 and Bonus 2 logic has been added, and whether the student
would leave it where it was or move it to optimize performance.

Expected answer: the Sort snap should be **moved to after the new
`MoreThan10K-Boolean == true` filter** (i.e. as close to the CSV
Formatter as possible, downstream of every row-reducing step).
Sorting is an O(n log n) operation, so sorting the smallest possible
dataset is cheapest; placing Sort upstream of a filter that throws
rows away wastes work. Any answer that conveys this idea —
"sort the smallest dataset" / "move Sort after the new filter so
fewer rows are sorted" / "filters before sort for performance" —
should be accepted.

Placement of the answer (version notes vs. sticky note vs. missing) is
governed by the universal rule in `general_evaluation_rules.md`.
