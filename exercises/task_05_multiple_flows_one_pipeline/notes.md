# Task 05 — Instructor Notes (AI guidance)

These notes are passed to the AI evaluator as *hints*, not strict rules.
The model uses them when deciding whether a difference between the
student and solution pipelines is meaningful.

The universal best-practice rules in
`exercises/general_evaluation_rules.md` apply to this exercise as-is —
do not restate them here. Only the task-specific equivalences below are
overrides; everything else in the general rules is in force.

## How the solution is built (for reference)

The official solution flows:

Read CSV → Parse CSV → **Sequence** (adds a record index) →
**Filter** (keep only even-index records) → **single Mapper**
(shape the needed fields) → **Router** (3 branches by the `ID`
condition) → 3× CSV Formatter → 3× Binary Write, one per report:

- `Trng_FilterEven.csv` — every even-index record
- `Trng_IDbelow15.csv` — even-index records with `ID < 15`
- `Trng_IDabove15.csv` — even-index records with `ID > 15`

All three output files are checked **exactly** by the output hard gate,
so any submission the AI sees has already produced the correct rows.
These notes only govern the **structural** differences the AI judges —
not the row data.

## Things that don't matter (task-specific — accepted equivalents)

Both design choices below are fully correct. Treat each as equivalent to
the solution and **deduct nothing** for either — including the universal
rules they might otherwise appear to trip.

- **Adding the record index — Sequence snap *or* Mapper with
  `snap.in.totalCount`.**
  The description explicitly says there are two ways to add an index. The
  solution uses a **Sequence** snap; a **Mapper** that derives the
  running index from `snap.in.totalCount` is equally valid. Accept either
  approach — **PASS, no deduction** for whichever the student chose.

- **Branch shaping — Filter + single Mapper *before* the Router, *or* a
  single Router followed by 3 identical Mapper snaps.**
  The solution keeps only the even-index records with a **Filter** and
  shapes them in **one Mapper** before a single **Router** fans the rows
  into the three report branches. An alternative that uses a single
  **Router** and then **3 identical Mapper snaps** (one on each output
  branch) **in place of** the Filter + single Mapper is equally correct —
  **PASS, no deduction.**
  In particular, the universal **"No extra Mapper snaps" (`-1`) rule does
  NOT apply** to those 3 per-branch Mappers in this exercise: here they
  are the intentional, accepted structure, not redundant snaps. Either
  way the pipeline is expected to use exactly **one Router** — that part
  does not change between the two approaches.
