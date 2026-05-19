# Task 01 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

## Things that matter

- **Snap order: filter must come BEFORE sort.**
  The CSV Parser feeds into a Filter (keep only California residents),
  and the Sort snap comes after the Filter. Sorting the full ~4000-row
  dataset and then filtering it is wasteful and counts as a real
  performance/best-practice issue. Flag this as **major** if the
  student has Sort placed before Filter.

- **The pipeline must unzip Task1.zip via a snap.**
  Manual unzipping is forbidden per the exercise description. If the
  student's pipeline has no ZipFile Read / unzip step and reads the
  uncompressed CSV directly, that is a **major** issue.

- **Sort order: last name, descending.**
  If the student sorts ascending, or sorts by the wrong column, that's
  a major issue (though it should also already be caught by the CSV
  output hard gate before the AI runs).

- **Output filter: California residents only.**
  Already caught by the CSV hard gate, but if the filter expression is
  visibly wrong in a way that *happens* to produce the right rows on
  this dataset (unlikely), flag it.

- **All snaps must be renamed from their default label.** Every snap
  in the pipeline should carry a descriptive name. The exact wording
  is up to the student and does not need to match the solution — it
  just must not be left as the default (e.g. `Mapper`, `Filter`,
  `Sort`, `ZipFile Read`). The intent is to enforce the discipline of
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
- Different but equivalent filter expressions (e.g. `$State == "CA"`
  vs `match("CA", $State)`).

## Bonus question

The exercise asks why the preview shows far fewer than 4000 records.
Expected answer: SnapLogic pipeline previews are limited to a default
sample size per snap (50 rows by default).

The student's answer **must be written in the pipeline version notes**
(the notes field on the pipeline version itself), not in a sticky note
on the canvas. When the answer is in the version notes, the AI should
summarize and assess it.

If the answer is only in a sticky note on the canvas but is otherwise
correct, flag it as a **minor** issue — the content is right but it
was placed in the wrong location. If the answer is missing entirely
(neither in version notes nor in a sticky note), treat the bonus as
not submitted and flag it as a **major** issue.
