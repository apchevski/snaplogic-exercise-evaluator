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
     (column-name set + row multiset; both column order and row order are
     ignored — reordered columns are realigned by name before comparing).
   - For triggered_task: (a) a Triggered Task named exactly
     `<pipeline name> Task` (the convention is strict — see
     [triggered-task-naming-strict](conventions/triggered-task-naming-strict.md))
     must exist in the student's project, and (b) every scenario's JSON
     response — invoked at grading time via the cloud URL — must
     structurally match the cached expected response.
   If any gate fails, the run aborts and writes a `verdict: "fail"`
   `evaluation.json` with the failing gate and detail. No AI step.

2. **AI judgment** — a **headless Claude API call** (`evaluator/ai_judge.py`),
   model `claude-sonnet-4-6` (locked decision). For each exercise whose
   hard gates passed (or output-mismatch-failed), the runner sends the
   `ai_context.json` bundle through the Messages API with structured
   outputs (`schemas/evaluation.schema.json`) and writes the
   `evaluation.json` contract. Points arithmetic and the verdict are
   recomputed in Python — the model only proposes differences with
   rule-sourced deduction values. The rules block is prompt-cached so a
   full run pays for the rule text once.

This split keeps the system simple AND auditable: the deterministic
layer catches the unambiguous failures (cheap and explainable), the AI
layer handles judgment calls (where rule-based code would have been
brittle).

## From skill to headless API (history)

The first design called Anthropic's API from Python; the second pivoted
judgment into a local Claude Code skill (`/grade`) so the user's Pro
subscription covered evaluations with no API key. In June 2026 the
project pivoted again — to **fully cloud-hosted grading** (see
[cloud_grading_plan.md](cloud_grading_plan.md)): mentors click Grade in a
web dashboard, a worker Lambda runs hard gates + the Claude API, nobody
installs anything. The skill's rubric text lives on in
`exercises/general_evaluation_rules.md`, per-task `notes.md`, and the
judge's system prompt in `evaluator/ai_judge.py`; the data interface is
unchanged (`ai_context.json` in, `evaluation.json` out), which is what
made the third pivot a drop-in: `evaluator/runner.py` drives the same
plan → judge → report loop the skill used to drive interactively.

## Cloud platform shape (June 2026)

- **Two Lambdas, one container image** (`Dockerfile`): the API
  Lambda (Powertools router, JWT-authorized HTTP API) writes JOB items +
  SQS messages; the worker Lambda consumes them (concurrency 1, DLQ with
  maxReceiveCount 1 — a paid grade job is never auto-retried).
- **Storage split**: authored exercise content ships in the image (git →
  CI rebuild); generated artifacts (solution.json, expected/, reconciled
  task.json) are gitignored and live in S3 under `exercises/<slug>/`,
  written only by prep jobs. `evaluator/store.py` materializes the merged
  tree under /tmp because the Lambda image filesystem is read-only
  (`evaluator/config.py` honors `EVALUATOR_*_DIR` env overrides).
- **Student input files** (`exercises/<slug>/resources/` — see
  `conventions/exercise-resources-folder.md`) are authored content, but
  they're too big to stream through a Lambda response (base64 + the 6 MB
  ceiling). `GET /v1/exercises/{slug}/resources/{filename}` lazily mirrors
  the image copy to S3 under `exercise-resources/<slug>/` (a prefix the
  worker's materialize step does NOT re-download, and the only prefix the
  API role can PutObject to) and returns a 5-minute presigned URL the
  browser downloads directly.
- **Reports are immutable versions** in S3 (`students/<slug>/<ver>/`);
  DynamoDB single table holds student cards, report history, job
  lifecycle, conditional-put locks (TTL 30 min), and exercise prep state.
- **Auth**: Cognito (admin-created users; groups `admin`/`mentor`) + API
  Gateway JWT authorizer; the Lambda re-checks source IP and enforces the
  role matrix (mentors get 403 on /v1/preps). IP allowlist also runs at
  the edge via a CloudFront Function.

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
- `resources/` (optional) — the input files handed to students (zips,
  CSVs). Listed and downloadable on the web UI's Exercises page; never
  loose in the exercise root.
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

## Containerization

`Dockerfile` + `docker-compose.yml` package **only the deterministic layer**
(the `evaluator/` package). The AI-judgment layer stays on the host inside the
operator's own AI assistant — baking an AI CLI + API key into the image would
contradict the "no Anthropic API key, no per-evaluation cost" goal above, so it
was deliberately left out (see the scope decision in `## Two-layer evaluation`).

Docker is the **primary, fully-supported runtime** (chosen 2026-06-10): the
`/prep` SKILL.md invokes `docker compose run --rm -T evaluator
python -m evaluator.…` rather than a local venv, so a fresh machine needs only
Docker. The venv remains a documented escape hatch (swap the
`docker compose run …` prefix for the interpreter).

**Pivot to cloud grading (decided 2026-06-11):** the local AI-judgment grading
flow was removed — the `/grade` SKILL.md, `AGENTS.md`, and
`.github/copilot-instructions.md` were deleted. Grading judgment moves to the
Claude API (Sonnet 4.6) running in AWS, triggered by a Grade button on a web
dashboard; the deterministic `evaluator/` layer and its rubric files
(`general_rules` + per-task `notes.md`) are reused by the cloud worker
unchanged in spirit. `/prep` stays a local admin task. See the implementation
plan for the full target architecture.

Consequences that the design leans on:
- The container and the host AI assistant **share the bind-mounted `.tmp/` +
  `grades/`**, which is how the three-step `/grade` handoff works across the
  boundary (`grade plan` in the container writes `ai_context.json`; the
  assistant on the host writes `evaluation.json`; `grade report` in the
  container reads it back). The bind-mount layout maps the host repo 1:1 onto
  `/app`, so a containerized run writes the same files a local run does.
- **Manifest paths are stored repo-root-relative** (`_rel_to_repo` /
  `_resolve_manifest_path` in `grade.py`), not absolute. `grade plan` runs in
  the container (CWD `/app`), but the host assistant must open the same
  `ai_context.json`; an absolute `/app/.tmp/...` would be meaningless on the
  host. Relative paths re-anchor correctly in both places (legacy absolute
  paths still pass through, so old manifests keep working).
- `task.json` is **committed** (env-neutral intent: `output_filename(s)`,
  `triggered_task_name`, `requests`); its one env-specific field
  (`solution_pipeline_path`) is rebuilt from `.env` + the description.md heading
  by `_proposed_path` on the next `prep sync`. The genuinely env-specific caches
  (`solution.json`, `solution.cache.json`, `expected/`, plus `grades/`, `ui/`,
  `.tmp/`, `.env`) stay `.dockerignore`d / gitignored and arrive via bind mounts
  or a live fetch.
- Runs non-root (`uid 10001`) with UTF-8 forced in the environment, because the
  non-`grade` entry points don't call the `sys.std*.reconfigure` shim that
  `evaluator.grade.main` does and would otherwise crash on en-dashes under the
  slim image's C locale.

This dovetails with the headless/CI expansion point below: an API-based AI path
could run inside the same image and read the same `ai_context.json`.

## Future expansion points

- Pipeline execution + output capture for triggered exercises.
- Bulk mode (grade an entire project space at once) — already partly
  feasible by globbing student projects.
- Pull SnapLogic's "Check pipeline quality" Public API output into the
  AI context bundle.
- Re-add an API-based AI path for headless/CI use without breaking the
  skill flow (both can read the same `ai_context.json`).
