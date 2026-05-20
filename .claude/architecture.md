# Architecture

## Goal

Automate evaluation of SnapLogic training exercises. The system must
scale to exercises that admit many correct solutions, which means the
final judgment has to come from a model rather than a hand-coded rubric.

## Two-layer evaluation

1. **Hard gates (deterministic)** — cheap, fast, fail-closed. Implemented
   in `evaluator/`, invoked via `python -m evaluator <slug> --student ...`.
   - Pipeline name must match the solution's exactly, with the single
     exception that the three dash glyphs (hyphen-minus, en dash, em
     dash) compare as equal — see
     [pipeline-name-dash-tolerant](conventions/pipeline-name-dash-tolerant.md).
   - For csv_writer: the student's CSV output must match the solution's
     (header + row multiset).
   - For triggered_task: (a) a Triggered Task named exactly
     `<pipeline name> Task` (the convention is strict — see
     [triggered-task-naming-strict](conventions/triggered-task-naming-strict.md))
     must exist in the student's project, and (b) every scenario's JSON
     response — invoked at grading time via the cloud URL — must
     structurally match the cached expected response.
   If any gate fails, the run aborts and writes a `verdict: "fail"`
   `evaluation.json` with the failing gate and detail. No AI step.

2. **AI judgment** — a **Claude Code skill** (`.claude/skills/grade/SKILL.md`),
   not an API call. When the user invokes `/grade <student>`, Claude (the
   model running in their Pro session) reads
   `.tmp/grades/<student>/<slug>/ai_context.json` for each exercise whose
   hard gates passed and produces the verdict directly. No Anthropic API
   key, no per-evaluation cost.

This split keeps the system simple AND auditable: the deterministic
layer catches the unambiguous failures (cheap and explainable), the AI
layer handles judgment calls (where rule-based code would have been
brittle).

## Why a skill, not an API call

The original design called Anthropic's API from Python. That requires
an API key billed per-token, separate from the user's Claude Code Pro
subscription. Pivoting the judgment step into a skill means:

- The user's existing Pro subscription covers all evaluations.
- Prompt iteration is editing markdown, not redeploying code.
- The Python orchestrator becomes strictly deterministic — easier to
  reason about, easier to test.

The trade-off: the system can't run headless (e.g., a nightly CI job
evaluating 50 students). When/if that becomes necessary, the AI step
can be re-implemented in Python alongside the skill. The data interface
(`.tmp/grades/<student>/<slug>/ai_context.json` in,
`.tmp/grades/<student>/<slug>/evaluation.json` out) is already designed
to accept either implementation.

## Canonical pipeline form

We do **not** maintain an internal `PipelineIR` Pydantic model. The
canonical form is the raw JSON returned by the SnapLogic REST API. The
solution copy is cached in the repo at `exercises/<slug>/solution.json`
(committed) with a `solution.cache.json` sidecar storing the asset's
modified-at signature; we only refetch the body when the signature
changes. The student copy is fetched fresh per run to
`.tmp/grades/<student>/<slug>/student/<name>.pipeline.json`.

Note: **never reason about snap execution order from `snap_map`**.
`snap_map` is a UUID-keyed dict; its iteration order is insertion order,
not flow order. Real execution flow lives in `link_map`. Use
`pipeline_fetch.flow_order()` / `flow_order_summary()` which Kahn-topo-sort
the link graph. (Bug fixed 2026-05-18 after the AI inferred order from
`snap_map` and got it wrong.)

## SnapLogic API usage

The client is **GET-only** by construction. Endpoints validated against
`elastic.snaplogic.com`:

- `GET /api/1/rest/asset/list/{org}/{ps}/{project}` → asset entries.
- `GET /api/1/rest/asset/{org}/{ps}/{project}/{name}` → asset metadata.
- `GET /api/1/rest/pipeline/{snode_id}` → full pipeline definition.
- `GET /api/1/rest/slfs/{org}/{ps}/{project}/{file}` (with `Accept: */*`,
  the default `application/json` triggers a 406) → file content from SLDB.
- `GET /api/1/rest/slsched/feed/{org}/{ps}/{project}/{task_name}` →
  invoke a Triggered Task with basic auth + query-string params; body
  is the pipeline's output (typically JSON). Used by /prep to capture
  solution responses for `triggered_task` exercises.

The Public-API `/catalog/...` endpoint is a paid feature; we don't use it.

## Pipeline execution for triggered-task exercises

Two execution models, selected per-exercise via `task_type` in
`task.json`:

- `csv_writer` (Task 01 etc.) — pipeline runs server-side at some point
  earlier; we fetch the already-produced CSV from SLDB for both
  solution and student. No execution at evaluation time.
- `triggered_task` (Task 02 onward) — pipeline is exposed as a
  SnapLogic Triggered Task. /prep invokes the solution task once per
  scenario in `task.json.requests` and saves each JSON response to
  `expected/<scenario_name>.json`. /grade does the same against the
  student's task. This DOES execute the pipeline server-side and
  counts against execution quota; the prep cache (sidecar keyed off
  the pipeline's `time_updated`) ensures re-runs are no-ops unless
  the pipeline definition changed.

The execution path is GET-only — Triggered Tasks expose a cloud URL
that accepts HTTP Basic auth in place of the per-task bearer token,
so `SnapLogicClient` stays GET-only by construction (see
`feedback_snaplogic_api_get_only`).

## Per-exercise registration

Each exercise lives at `exercises/<slug>/` with:
- `task.json` (required) — shape depends on `task_type`:
  - `csv_writer` (default for back-compat): `{ task_type, solution_pipeline_path, output_csv_filename }`
  - `triggered_task`: `{ task_type, solution_pipeline_path, triggered_task_name, requests }`
    where `requests` is a non-empty list of `{ name, params }` scenarios
    (each `name` becomes a filename in `expected/<name>.json`).
- `description.md` (required) — student-facing prompt
- `notes.md` (optional) — instructor hints fed to the AI judge.
  For triggered_task exercises this is also where the canonical
  Triggered Task name and scenario list live (in prose); /prep reads
  notes.md to derive task.json's `triggered_task_name` and `requests`.

The Python loader (`evaluator/tasks.py`) globs `exercises/*/task.json`.
Adding a new exercise = drop a folder. No code changes.

## CLI surface

Python orchestrator (deterministic only):
```
python -m evaluator <task_slug> --student "Org/PS/Project/Name" [--refresh-solution] [--student-name <name>]
```

Claude Code skill (full flow, one student, all exercises):
```
/grade <student name>
/grade --space <project space> <student name>
```

Outputs:
- `exercises/<slug>/solution.json` + `solution.cache.json` — repo-cached
  solution pipeline JSON and its signature sidecar. Committed to git.
- `exercises/<slug>/expected/...` — golden output cache.
  - csv_writer: one CSV named by `output_csv_filename`.
  - triggered_task: one JSON file per scenario, `<request_name>.json`.
  /prep deletes any other files in `expected/` during reconcile, so
  stale outputs from renamed writers or removed scenarios don't
  accumulate.
- `grades/<student>/report.md` — **persistent** aggregated human-readable
  report. Only file that survives a `/grade` run.
- `.tmp/grades/<student>/...` — **scratch only**, deleted at the end of
  `evaluator.grade report`. Holds (during a run):
  - `manifest.json` — per-run task index consumed by the skill.
  - `<slug>/ai_context.json` — bundle the skill consumes when hard gates pass.
  - `<slug>/evaluation.json` — per-exercise verdict produced by the skill.
  - `<slug>/student/` — fetched student pipeline JSON + student CSV.

## Future expansion points

- Pipeline execution + output capture for triggered exercises.
- Bulk mode (grade an entire project space at once) — already partly
  feasible by globbing student projects.
- Pull SnapLogic's "Check pipeline quality" Public API output into the
  AI context bundle.
- Re-add an API-based AI path for headless/CI use without breaking the
  skill flow (both can read the same `ai_context.json`).
