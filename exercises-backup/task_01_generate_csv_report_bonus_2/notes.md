# Task 01 Bonus 2 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

This exercise builds directly on **Bonus 1** (which itself builds on
Task 01). The student copies their Bonus 1 pipeline into a new
pipeline and adds a **Domain** column to the output. The Domain value
is the portion of the email starting at the `@` character (the `@` is
included). The universal best-practice rules in
`general_evaluation_rules.md` and all Task 01 / Bonus 1 task-specific
rules still apply — only the points below are specific to this bonus.

## Things that matter (new for Bonus 2)

- **Output must contain a Domain column. `-2 points`.**
  Already enforced by the CSV output hard gate as a FAIL, but if the
  output happens to match without a Domain mapping (unlikely),
  deduct **`-2`**.

- **Domain value must include the `@` character. `-2 points`.**
  Per the exercise text: for `andrej.bogdanovski@iwconnect.com` the
  expected Domain value is `@iwconnect.com`, **not** `iwconnect.com`.
  If the student strips the `@` and outputs only the bare domain,
  deduct **`-2`** (the CSV hard gate will normally catch this as a
  FAIL; the deduction applies only if the gate passed).

  Acceptable expression styles include
  `$Email.substring($Email.indexOf("@"))`,
  `"@" + $Email.split("@")[1]`, or any other expression that yields
  the `@`-prefixed domain.

- **Domain must be added to the existing Mapper carried over from
  Bonus 1. `-1 point`.** The Bonus 1 pipeline already has a Mapper
  shaping the output rows; the Domain field belongs as another
  mapping inside that same snap, not in a brand-new Mapper. (This is
  a task-specific application of the universal "no extra Mapper
  snaps" and "Mapper Pass through" rules — apply this rule once,
  not both.) Deduct **`-1`** if the student introduced a separate
  Mapper for Domain instead of extending the existing one.

## Things that don't matter (new for Bonus 2)

- The exact expression used to extract the domain, as long as the
  resulting value matches.
