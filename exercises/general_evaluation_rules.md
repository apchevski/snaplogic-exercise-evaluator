# General Evaluation Rules

These rules apply to **every** exercise. The orchestrator enforces them as
hard gates before the AI evaluator is called. If any rule fails, the
submission is marked **FAIL** and no AI tokens are spent.

## Hard rules (deterministic, applied before AI)

1. **Pipeline name must match exactly.**
   The student's pipeline name must be an exact, case-sensitive match of
   the solution pipeline's name. Trailing/leading whitespace counts.

2. **Output files must match exactly.**
   When an exercise produces an output file (e.g. a CSV), the student's
   output must match the solution's output. Compared header-aware and
   as a row multiset (order-insensitive at this gate — pipeline-level
   ordering choices are evaluated by the AI on the pipeline structure).

If either hard rule fails → **automatic FAIL**. The AI evaluator is not
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
- `expected/` — golden output files (auto-populated on first run by
  pulling the solution pipeline's output via the SnapLogic API).

To register a new exercise, add a `TaskConfig` entry in
`src/evaluator/evaluate.py`. (Future: load these from a `task.json`.)
