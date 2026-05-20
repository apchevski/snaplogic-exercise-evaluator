# General Evaluation Rules

These rules apply to **every** exercise. The orchestrator enforces them as
hard gates before the AI evaluator is called. If any rule fails, the
submission is marked **FAIL** and no AI tokens are spent.

## Hard rules (deterministic, applied before AI)

1. **Pipeline name must match exactly.**
   The student's pipeline name must be an exact, case-sensitive match of
   the solution pipeline's name. Trailing/leading whitespace counts.

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
   name still fails this gate.

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

## Adding a new exercise

Each exercise lives under `exercises/<slug>/` and may contain:

- `description.md` — the student-facing exercise text (required).
- `notes.md` — instructor hints fed to the AI as guidance (optional).
- `task.json` — exercise registration; shape depends on `task_type`
  (`csv_writer` or `triggered_task`). See `evaluator/tasks.py` for the
  full schema. /prep bootstraps this for you.
- `expected/` — golden output files (auto-populated by /prep).
  - `csv_writer`: one CSV named by `output_csv_filename`.
  - `triggered_task`: one JSON file per scenario in `requests`.

To register a new exercise, drop a folder with `description.md` and run
`/prep`. The Python loader (`evaluator/tasks.py`) globs
`exercises/*/task.json`; no code changes needed.
