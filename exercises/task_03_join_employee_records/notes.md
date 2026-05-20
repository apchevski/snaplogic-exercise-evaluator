# Task 03 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

## Things that matter

- **Snap order: filter must come BEFORE sort.**
  The Leads stream is filtered down to San Francisco residents and then
  sorted by Zip code. Sorting the full Leads dataset and then filtering
  is wasteful and counts as a real performance/best-practice issue.
  Flag this as **major** if the student has Sort placed before Filter.

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
  that's a major issue (though it should also already be caught by the
  CSV output hard gate before the AI runs).

- **All snaps must be renamed from their default label.** Every snap
  in the pipeline should carry a descriptive name. The exact wording
  is up to the student and does not need to match the solution — it
  just must not be left as the default (e.g. `Mapper`, `Filter`,
  `Sort`, `Join`). The intent is to enforce the discipline of
  labeling snaps so a reader can follow the pipeline at a glance.
  **Exception:** the CSV Parser and CSV Formatter snaps are allowed
  to keep their default names — those defaults already describe what
  the snap does. Flag as **minor** if any other snap is left with its
  default name.

- **CSV Formatter must have "Ignore empty stream" checked.**
  Without it, the pipeline will write an empty output file when the
  upstream filter produces no rows, which is undesirable behavior. We
  do not want to emit empty data. Flag as **minor** if the option is
  unchecked.

- **No extra Mapper snaps.**
  Every Mapper in the pipeline must serve a real purpose. Mapper
  snaps inserted purely for visual clarity or that pass data through
  unchanged should not be present. Flag as **minor** if there are
  unused or pass-through Mapper snaps.

## Things that don't matter

- Snap label positions and view-layout coordinates (but snap *names*
  do matter — see above).
- Different but equivalent filter expressions (e.g. `$City == "San Francisco"`
  vs `match("San Francisco", $City)`).
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

The student's answer **must be written in the pipeline version notes**
(the notes field on the pipeline version itself), not in a sticky note
on the canvas. When the answer is in the version notes, the AI should
summarize and assess it.

If the answer is only in a sticky note on the canvas but is otherwise
correct, flag it as a **minor** issue — the content is right but it
was placed in the wrong location. If the answer is missing entirely
(neither in version notes nor in a sticky note), treat the bonus as
not submitted and flag it as a **major** issue.
