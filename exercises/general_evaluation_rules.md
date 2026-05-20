# General Evaluation Rules

These rules apply to **every** exercise. The orchestrator enforces the
**hard rules** as deterministic gates before the AI evaluator is called —
if any hard rule fails, the submission is marked **FAIL** and no AI
tokens are spent. The **soft rules** are passed to the AI evaluator as
universal hints; a per-task `notes.md` may override or extend any soft
rule when the exercise calls for it.

## Hard rules (deterministic, applied before AI)

1. **Pipeline name must match exactly (dash glyphs are interchangeable).**
   The student's pipeline name must be a character-for-character,
   case-sensitive match of the solution pipeline's name, except that the
   three dash glyphs — hyphen-minus `-` (U+002D), en dash `–` (U+2013),
   and em dash `—` (U+2014) — compare as equal. So
   `Task 03 – Join Employee Records` (en dash) and
   `Task 03 - Join Employee Records` (hyphen) are the same name.
   Trailing/leading whitespace and all other punctuation still count.

2. **(csv_writer) Output CSV must match exactly.**
   When an exercise produces a CSV via a binary-write snap, the
   student's output must match the solution's. Compared header-aware
   and as a row multiset (order-insensitive at this gate — pipeline-
   level ordering choices are evaluated by the AI on the pipeline
   structure).

3. **(triggered_task) Triggered Task must exist with the convention name.**
   For triggered-task exercises, a Triggered Task named exactly
   `<pipeline name> Task` must exist in the student's project. The
   convention is strict — a correctly-behaving task under a different
   name still fails this gate. The same dash-glyph tolerance from rule 1
   applies: hyphen-minus, en dash, and em dash compare as equal, but no
   other deviation is allowed.

4. **(triggered_task) Every scenario response must match.**
   Each scenario in `task.json`'s `requests` array is invoked against
   the student's Triggered Task and the response body is compared
   structurally (as parsed JSON) against the cached expected response.
   Any scenario whose response differs — or whose invocation returns
   a non-2xx status — fails the gate.

If any hard rule fails → **automatic FAIL**. The AI evaluator is not
invoked.

## Soft rules (AI-driven, applied when hard gates pass)

When hard gates pass, the AI evaluator (Claude Opus 4.7) compares the
two pipelines' SnapLogic JSON definitions. It is told:

- There is usually more than one correct way to solve an exercise.
- Penalize only meaningful problems (incorrect logic, real bad practice,
  violations of explicit instructor guidance).
- Do NOT penalize stylistic differences, naming, or structurally
  different snaps that achieve the same correct outcome.

The AI returns a verdict of `pass`, `pass_with_minor_issues`, or `fail`.
A `fail` from the AI carries the same weight as a hard-gate fail.

### Universal best-practice rules

These rules apply to **every** exercise. They are general SnapLogic best
practices we expect across all submissions. A task's own `notes.md` can
override or extend any of them — if a task does not override, the rule
is in force. The AI judge should evaluate against these rules whenever
the relevant snap type is present in the student's pipeline.

- **Filter before sort (row-reducing snaps before expensive snaps).**
  Filter, conditional row-drop, or any other row-reducing snap belongs
  *upstream* of expensive snaps like Sort, Join, and Aggregate. Sorting
  or joining a full dataset and then throwing rows away is wasteful and
  counts as a real performance/best-practice issue. Flag as **major**
  if Sort sits before a Filter that materially reduces row count, and
  more broadly if any row-reducing step is downstream of an expensive
  snap it could have preceded. *Override:* a task may legitimately
  require the opposite order (e.g. when the filter depends on a value
  produced by the upstream snap) — in that case the task's `notes.md`
  will say so explicitly.

- **CSV Formatter must have "Ignore empty stream" checked.**
  Whenever the pipeline ends in a CSV Formatter, the
  *Ignore empty stream* option must be enabled. Without it, the
  pipeline writes an empty output file when the upstream filter
  produces no rows, which is undesirable behavior — we do not want to
  emit empty data. Flag as **minor** if the option is unchecked.

- **Mapper snaps must use "Pass through" and only declare the fields
  that are added, changed, or removed.** When a Mapper is used to add
  or modify a column, it must have *Pass through* enabled so that
  existing fields flow downstream automatically; the Mapper's expression
  table should contain only the new/changed/removed mappings, not a
  re-declaration of every pre-existing field. Manually re-mapping every
  field is verbose, brittle (any upstream schema change silently breaks
  the Mapper instead of flowing through), and obscures what actually
  changed in this snap. Flag as **minor** if a Mapper re-declares
  pre-existing fields instead of relying on Pass through.

- **No extra Mapper snaps.**
  Every Mapper in the pipeline must serve a real purpose. Mapper snaps
  inserted purely for visual clarity, or that pass data through
  unchanged, should not be present. When the pipeline already has a
  Mapper shaping the output, a new field belongs *inside* that same
  Mapper rather than in a second one bolted on after it. Flag as
  **minor** if there are unused or pass-through Mapper snaps, or if a
  second Mapper exists only to add a single field that could have been
  added to an existing Mapper.

- **All snaps must be renamed from their default label.**
  Every snap in the pipeline should carry a descriptive name. The exact
  wording is up to the student and does not need to match the solution
  — it just must not be left as the default (e.g. `Mapper`, `Filter`,
  `Sort`, `Join`, `Router`, `Union`, `Conditional`, `ZipFile Read`).
  The intent is to enforce the discipline of labeling snaps so a reader
  can follow the pipeline at a glance. **Exception:** the CSV Parser
  and CSV Formatter snaps are allowed to keep their default names —
  those defaults already describe what the snap does. Flag as **minor**
  if any other snap is left with its default name.

- **Bonus question answers must live in the pipeline version notes.**
  For any exercise that includes a Bonus Question (in `description.md`),
  the student's written answer **must** appear in the pipeline version
  notes (the notes field on the pipeline version itself), not in a
  sticky note on the canvas. When the answer is in the version notes,
  the AI should summarize and assess its correctness against the
  task-specific expected answer in `notes.md`. If the answer is only
  in a sticky note on the canvas but otherwise correct, flag as
  **minor** (right content, wrong location). If the answer is missing
  entirely (neither in version notes nor in a sticky note), treat the
  bonus as not submitted and flag as **major**.

### Universal "things that don't matter"

Apply to every exercise unless a `notes.md` says otherwise:

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
  when the exercise legitimately requires it.
- `task.json` — exercise registration; shape depends on `task_type`
  (`csv_writer` or `triggered_task`). See `evaluator/tasks.py` for the
  full schema. /prep bootstraps this for you.
- `expected/` — golden output files (auto-populated by /prep).
  - `csv_writer`: one CSV named by `output_csv_filename`.
  - `triggered_task`: one JSON file per scenario in `requests`.

To register a new exercise, drop a folder with `description.md` and run
`/prep`. The Python loader (`evaluator/tasks.py`) globs
`exercises/*/task.json`; no code changes needed.
