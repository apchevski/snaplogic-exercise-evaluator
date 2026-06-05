# General Evaluation Rules

These rules apply to **every** exercise. The orchestrator enforces the
**hard rules** as deterministic gates before the AI evaluator is called —
if any hard rule fails, the submission is marked **FAIL** and no AI
tokens are spent. The **soft rules** are passed to the AI evaluator as
universal hints; a per-task `notes.md` may override or extend any soft
rule when the exercise calls for it.

## Verdicts and points

Every exercise resolves to exactly one of three verdicts:

- **PASS** — every hard gate passed; the student receives **10 points
  minus any rule-based deductions** listed below. **Floor is 0, never
  negative — the verdict stays PASS even when deductions sum past 10
  (the output matches the solution; points just clamp to 0).**
- **FAIL** — the student's pipeline output does not match the
  solution. Points depend on **why** the gate failed:
  - **Output-mismatch FAIL** (`csv_output_match` or
    `triggered_task_responses_match`): the AI judge **is** invoked and
    awards partial points for pipeline structure — the verdict stays
    FAIL (output is wrong) but the score reflects how close the
    pipeline is to a correct solution. Same rule deductions apply, same
    floor-at-0 rule.
    Rationale: a student whose pipeline is correct except for one
    misspelled string literal should not be ranked alongside a
    student who submitted an empty pipeline.
  - **Procedural FAIL** (pipeline name doesn't match): **0 points**,
    AI not invoked. The deliverable IS there but doesn't follow the
    naming convention, so nothing partial to credit and no need to
    spend AI time on it.
- **MISSING** — the student did not submit a gradable deliverable.
  Three cases trigger MISSING:
  - No pipeline matching the solution name exists in the student's
    project (the planner can't find anything to grade).
  - **csv_writer:** the pipeline exists but the expected output file
    isn't in SLDB (`csv_output_present` 404 — the student never ran
    their pipeline, so there's no output to compare).
  - **triggered_task:** the pipeline exists but there's no Triggered
    Task with the convention name `<pipeline name> Task` in the
    student's project (`triggered_task_exists` failed — the student
    didn't create the deliverable that lets the task be invoked).
  In any of these cases the exercise is **not graded** and carries
  **no point value** (neither 0 nor 10) — it is shown in the report
  with a `—` for points and excluded from totals.

The AI judge can produce PASS or FAIL (the latter only when the
orchestrator routed an output-mismatch failure to it). It cannot
produce MISSING (that is the orchestrator's job). For procedural
FAILs and MISSING-by-no-output the orchestrator writes the artifact
directly without invoking the AI.

### Point-deduction rules (apply to every rule below)

1. **Deductions come from the rule itself.** Every rule that can cost
   points states its value explicitly (`-2`, `-1`, or *mention only*).
   The AI never invents a deduction value. If a rule has no point value
   attached, it is *mention only* — the issue is surfaced under
   **Notes** in the report, but nothing is deducted.
2. **Same mistake → same deduction, every time.** The same rule
   violation must cost the same number of points across every student
   and every exercise. If a rule is worth `-2`, it is `-2` for every
   submission where it applies. Consistency is more important than
   nuance.
3. **One rule, one deduction per exercise.** If the student violates
   the same rule in two places within the same exercise (e.g. two
   default-named snaps), deduct the rule's value **once**, not per
   occurrence. Mention all occurrences in the description.
4. **Issues with no governing rule are mentioned, not deducted.** If
   the AI sees something that looks off but is not covered by a rule
   below or in the task's `notes.md`, surface it under **Notes** with
   no deduction. Inventing a point value silently breaks consistency
   across students.

## Hard rules (deterministic, applied before AI → verdict FAIL on failure)

Each gate is tagged **[procedural]** (0 points on failure, AI not
invoked) or **[output-mismatch]** (AI judges for partial credit;
verdict still FAIL).

1. **[procedural] Pipeline name must match exactly (dash glyphs are interchangeable).**
   The student's pipeline name must be a character-for-character,
   case-sensitive match of the solution pipeline's name, except that the
   three dash glyphs — hyphen-minus `-` (U+002D), en dash `–` (U+2013),
   and em dash `—` (U+2014) — compare as equal. So
   `Task 03 – Join Employee Records` (en dash) and
   `Task 03 - Join Employee Records` (hyphen) are the same name.
   Trailing/leading whitespace and all other punctuation still count.

2. **[MISSING] (csv_writer) Output file must exist in SLDB.**
   If the student never ran their pipeline, the output CSV doesn't
   exist (HTTP 404 from `/slfs/...`). There's nothing to compare and
   nothing for the AI to judge — treated as **MISSING** (excluded
   from totals), not as a 0-point FAIL. The student didn't submit a
   gradable deliverable.

3. **[output-mismatch] (csv_writer) Output CSV must match exactly.**
   When an exercise produces a CSV via a binary-write snap, the
   student's output must match the solution's. Compared header-aware
   and as a row multiset (order-insensitive at this gate — pipeline-
   level ordering choices are evaluated by the AI on the pipeline
   structure). If this gate fails, the AI is invoked to judge the
   pipeline structure and award partial points; the verdict stays
   FAIL.
   **Columns-only override.** A task whose output is inherently
   non-deterministic (e.g. a pipeline that calls an API returning random
   rows every run) can set `"output_match_mode": "columns_only"` in its
   `task.json`. In that mode this gate compares only the **column header**
   (exact, order-sensitive) and ignores the row data — so a structurally
   correct submission still **passes** the gate. The header reader is
   format-aware: real `.xlsx` output (SnapLogic's Excel Formatter) is
   parsed for its first worksheet row, everything else as CSV. Default is
   `"exact"`. See `task_04_born_on_friday`.

4. **[MISSING] (triggered_task) Triggered Task must exist with the convention name.**
   For triggered-task exercises, a Triggered Task named exactly
   `<pipeline name> Task` must exist in the student's project. The
   convention is strict — a correctly-behaving task under a different
   name still fails this gate. The same dash-glyph tolerance from rule 1
   applies: hyphen-minus, en dash, and em dash compare as equal, but no
   other deviation is allowed. Failure here resolves to **MISSING**
   (excluded from totals), not a 0-point FAIL — the student didn't
   submit a runnable deliverable, so there's nothing to grade.

5. **[output-mismatch] (triggered_task) Every scenario response must match.**
   Each scenario in `task.json`'s `requests` array is invoked against
   the student's Triggered Task and the response body is compared
   structurally (as parsed JSON) against the cached expected response.
   Any scenario whose response differs — or whose invocation returns
   a non-2xx status — fails the gate. The AI is then invoked to judge
   the pipeline structure (and the per-scenario diffs) and award
   partial points; the verdict stays FAIL.

If a procedural gate fails → **verdict FAIL, 0 points**, AI not
invoked. If `csv_output_present` or `triggered_task_exists` fails →
**verdict MISSING**, AI not invoked, exercise excluded from totals.
If an output-mismatch gate fails → **verdict FAIL**, AI judges the
pipeline and assigns partial points using the same rule deductions
below.

## Soft rules (AI-driven, applied when hard gates pass)

When hard gates pass, the AI evaluator compares the two pipelines'
SnapLogic JSON definitions and deducts points using the rules below.
It is told:

- There is usually more than one correct way to solve an exercise.
- Apply only the deductions listed in these rules and the task's
  `notes.md`. Never invent a new point value.
- Do NOT penalize stylistic differences, naming, or structurally
  different snaps that achieve the same correct outcome.

### Universal best-practice rules

These rules apply to **every** exercise. They are general SnapLogic best
practices we expect across all submissions. A task's own `notes.md` can
override or extend any of them — if a task does not override, the rule
is in force. The AI judge should evaluate against these rules whenever
the relevant snap type is present in the student's pipeline.

- **Filter before sort (row-reducing snaps before expensive snaps).
  `-2 points`.** Filter, conditional row-drop, or any other row-reducing
  snap belongs *upstream* of expensive snaps like Sort, Join, and
  Aggregate. Sorting or joining a full dataset and then throwing rows
  away is wasteful and counts as a real performance/best-practice
  issue. Deduct **`-2`** if Sort sits before a Filter that materially
  reduces row count, or more broadly if any row-reducing step is
  downstream of an expensive snap it could have preceded. *Override:*
  a task may legitimately require the opposite order (e.g. when the
  filter depends on a value produced by the upstream snap) — in that
  case the task's `notes.md` will say so explicitly.

- **CSV Formatter must have "Ignore empty stream" checked.
  `-1 point`.** Whenever the pipeline ends in a CSV Formatter, the
  *Ignore empty stream* option must be enabled. Without it, the
  pipeline writes an empty output file when the upstream filter
  produces no rows, which is undesirable behavior — we do not want to
  emit empty data. Deduct **`-1`** if the option is unchecked.

- **Mapper snaps must use "Pass through" and only declare the fields
  that are added, changed, or removed. `-1 point`.** When a Mapper is
  used to add or modify a column, it must have *Pass through* enabled
  so that existing fields flow downstream automatically; the Mapper's
  expression table should contain only the new/changed/removed
  mappings, not a re-declaration of every pre-existing field.
  Re-passing every field manually — whether by listing each field
  one-by-one *or* by using a single expression that reproduces `$`
  (e.g. a lambda like `$.filter((value, key) => ...)`, a spread
  `{...$, newField: ...}`, or any other technique that yields the
  same effect) — is verbose, brittle (any upstream schema change
  silently breaks the Mapper instead of flowing through), and
  obscures what actually changed in this snap. **Deduct `-1` if the
  Mapper has *Pass through* disabled and the result is anything
  other than a deliberate projection to a new schema (i.e. it still
  carries the same fields forward via the expression table or via a
  whole-document expression).** The deliberate-projection
  exception: if the Mapper exists to reshape the row into a totally
  new set of fields (e.g. Task 01's Mapper that renames `SURNAME`/
  `GIVENNAME`/`BIRTHDAY` into a 3-column report schema), Pass
  through is correctly disabled and no deduction applies.

- **No extra Mapper snaps. `-1 point`.**
  Every Mapper in the pipeline must serve a real purpose. Mapper snaps
  inserted purely for visual clarity, or that pass data through
  unchanged, should not be present. When the pipeline already has a
  Mapper shaping the output, a new field belongs *inside* that same
  Mapper rather than in a second one bolted on after it. Deduct
  **`-1`** if there are unused or pass-through Mapper snaps, or if a
  second Mapper exists only to add a single field that could have been
  added to an existing Mapper.

- **All snaps must be renamed from their default label. `-1 point`.**
  Every snap in the pipeline should carry a descriptive name. The exact
  wording is up to the student and does not need to match the solution
  — it just must not be left as the default (e.g. `Mapper`, `Filter`,
  `Sort`, `Join`, `Router`, `Union`, `Conditional`, `ZipFile Read`).
  The intent is to enforce the discipline of labeling snaps so a reader
  can follow the pipeline at a glance. **Exception:** the CSV Parser
  and CSV Formatter snaps are allowed to keep their default names —
  those defaults already describe what the snap does. Deduct **`-1`**
  if any other snap is left with its default name (one deduction
  total, no matter how many default-named snaps there are — name them
  all in the description).

- **Bonus question answers must live in the pipeline version notes.**
  For any exercise that includes a Bonus Question (in `description.md`),
  the student's written answer **should** appear in the pipeline version
  notes (the notes field on the pipeline version itself). The AI should
  summarize and assess the answer's correctness against the task-specific
  expected answer in `notes.md`. Three distinct cases:
  - **Answer present in the version notes and correct**: no deduction.
  - **Answer present but in the wrong place** (sticky note on the
    canvas, snap-level notes, or info.notes — anywhere except the
    version notes): *mention only*, **no deduction**. Flag the
    placement in **Notes** so the student knows the canonical location,
    but don't punish a correct answer for being filed in the wrong
    field.
  - **Answer missing entirely OR wrong** (not present anywhere we
    check, or present but does not match the expected idea): deduct
    **`-2`** — the bonus is not submitted (or the student's
    understanding is incorrect).

- **Zip inputs must use a ZipFile Read snap. `-5 points`.**
  If a task's input file is a zip archive, the pipeline **must**
  extract it with a ZipFile Read (or equivalent unzip) snap. Reading
  an already-extracted CSV directly — meaning the student unzipped
  the file manually outside the pipeline — defeats the purpose of the
  exercise and is a major violation of the workflow. Deduct **`-5`**.
  This is one of the few deductions worth more than 2 points because
  it represents skipping a core requirement of the task, not a
  best-practice slip.

### Universal "things that don't matter" (mention only, no deduction)

Apply to every exercise unless a `notes.md` says otherwise. These
never cost points; flag in **Notes** only if they're worth telling
the student about.

- Snap label positions, view-layout coordinates, and other purely
  visual canvas details (but snap *names* do matter — see above).
- Different but equivalent expressions for the same logical operation
  (e.g. `$State == "CA"` vs `match("CA", $State)`,
  `$Email.substring($Email.indexOf("@"))` vs `"@" + $Email.split("@")[1]`).

## Adding a new exercise

Each exercise lives under `exercises/<slug>/` and may contain:

- `description.md` — the student-facing exercise text (required).
- `notes.md` — instructor hints fed to the AI as guidance (optional).
  Only put **task-specific** rules here; the universal rules above
  apply automatically. Use `notes.md` to override a universal rule
  when the exercise legitimately requires it. **Every rule in
  `notes.md` that can cost points must state its value explicitly
  (`-2 points`, `-1 point`, or *mention only*).** Without an explicit
  value, the AI defaults to *mention only* — consistency across
  students depends on the rule, not the judge.
- `task.json` — exercise registration; shape depends on `task_type`
  (`csv_writer` or `triggered_task`). See `evaluator/tasks.py` for the
  full schema. /prep bootstraps this for you.
- `expected/` — golden output files (auto-populated by /prep).
  - `csv_writer`: one CSV named by `output_csv_filename`.
  - `triggered_task`: one JSON file per scenario in `requests`.

To register a new exercise, drop a folder with `description.md` and run
`/prep`. The Python loader (`evaluator/tasks.py`) globs
`exercises/*/task.json`; no code changes needed.
