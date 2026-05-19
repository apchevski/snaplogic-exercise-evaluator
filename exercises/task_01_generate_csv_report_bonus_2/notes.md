# Task 01 Bonus 2 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

This exercise builds directly on **Bonus 1** (which itself builds on
Task 01). The student copies their Bonus 1 pipeline into a new
pipeline and adds a **Domain** column to the output. The Domain value
is the portion of the email starting at the `@` character (the `@` is
included). All Task 01 and Bonus 1 rules still apply — only the
points below are specific to this bonus.

## Things that matter (new for Bonus 2)

- **Output must contain a Domain column.**
  Already enforced by the CSV output hard gate, but worth flagging at
  the pipeline level: if the Mapper does not produce a Domain field
  alongside the existing last name / first name / birthday columns,
  flag as **major**.

- **Domain value must include the `@` character.**
  Per the exercise text: for `andrej.bogdanovski@iwconnect.com` the
  expected Domain value is `@iwconnect.com`, **not** `iwconnect.com`.
  If the student strips the `@` and outputs only the bare domain,
  flag as **major**. (The CSV hard gate will already catch the
  mismatch — name the cause in the pipeline review.)

  Acceptable expression styles include
  `$Email.substring($Email.indexOf("@"))`,
  `"@" + $Email.split("@")[1]`, or any other expression that yields
  the `@`-prefixed domain.

- **Domain must be added to the existing Mapper carried over from
  Bonus 1 — do not introduce a new Mapper snap for it.** The Bonus 1
  pipeline already has a Mapper shaping the output rows; the Domain
  field should be added as another mapping inside that same snap.
  Adding a second Mapper snap whose only job is to produce the
  Domain field is not best practice — it splits closely-related
  output-shaping logic across two snaps for no benefit. This is also
  consistent with the Task 01 "no extra Mapper snaps" rule. Flag as
  **minor** if the student introduced a separate Mapper for Domain
  instead of extending the existing one.

## Things that don't matter (new for Bonus 2)

- The exact expression used to extract the domain, as long as the
  resulting value matches.
