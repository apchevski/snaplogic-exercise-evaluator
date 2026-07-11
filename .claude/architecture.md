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
- **Storage (July 2026 pivot): S3 is the source of truth for exercise
  content.** Everything of an exercise lives in S3 under
  `exercises/<slug>/` — authored files (`description.md`, `notes.md`,
  `resources/*`, written by the API's create/edit routes) and generated
  artifacts (`task.json`, `solution.json`, `expected/`, written only by
  sync jobs; the API's PutObject IAM is scoped to exactly the authored
  filename patterns, DeleteObject to `resources/*` only). Type-specific
  config that used to be a hand-written task.json is structured data on
  the EXERCISE DynamoDB row (`task_config`); the worker synthesizes
  task.json from it before every sync. The repo's `exercises/`
  folders are a **create-only seed**: sync jobs additively migrate their
  authored files to S3 (`S3Store.seed_authored_files`, never overwrites,
  never deletes) and the S3 copy wins everywhere afterwards — both in the
  materialize overlay and in the Exercises listing. `evaluator/store.py`
  still materializes the merged tree under /tmp (image → S3 overlay)
  because the Lambda image filesystem is read-only. The API detects
  authored slugs by the presence of `exercises/<slug>/description.md` in
  S3 (sync never uploads one). Input files travel browser ↔ S3 via
  presigned URLs (bucket CORS allows PUT from the SPA origins).
  **Archive is the reversible delete**: a soft flag on the row; the worker
  prunes archived folders from its working tree after materialize (out of
  sync, grading, and the points denominator) while S3 keeps everything.
  Durability: bucket versioning (noncurrent 90 days) + table PITR +
  `prevent_destroy` on both, plus the nightly one-way
  `backup-exercises.yml` snapshot into `exercises-backup/` in the repo.
- **Hard deletes (July 2026): admin-only, purge every trace.** The user's
  requirement was "no tracks left in AWS", so `DELETE /v1/students/{slug}`
  and `DELETE /v1/exercises/{slug}` purge **all S3 object versions** under
  the entity's prefixes (the versioning insurance is deliberately bypassed
  for an explicit delete; the UI confirmation dialog is the safety), all
  DynamoDB rows (card + report history / exercise row), the entity's job
  rows and lock, with a 409 while a job for the target is in flight.
  Exercise deletion also scrubs the task from every student's **live**
  report (json + md section, counts/points recomputed with grade.py's own
  helpers, card refreshed); historical report versions are the students'
  grading history and are kept. **Tombstones:** an exercise whose folder
  still ships in the image keeps a minimal `deleted: true` row — without
  it the image copy would resurface in listings and be re-seeded to S3 by
  the next sync (`worker._prune_excluded_exercises` prunes tombstoned
  slugs like archived ones). S3-authored-only exercises delete cleanly
  with no tombstone. `POST /v1/exercises` on a tombstoned slug re-creates
  it. Out of reach by design: CloudWatch log lines (retention handles
  them) and prior `exercises-backup/` snapshots in git history.
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
  lifecycle, conditional-put locks (TTL 30 min), and exercise sync state.
- **The STUDENT card is the source of truth for where grading looks
  (July 2026).** Registration (the Add Student dialog) stores `space`
  (project space, resolved to the env default if left blank) and
  optionally `project` (SnapLogic project name, when it isn't named
  exactly after the student) on the card. `POST /v1/gradings` resolves
  `body override → card → env default` into the job payload; the worker
  passes both into `run_grade` and writes them back on the card refresh
  (backfilling legacy cards on their next grading).
  `SNAPLOGIC_STUDENT_PROJECT_SPACE` is only the default that prefills the
  dialog (exposed via `GET /v1/config`, which returns non-secret settings
  only). There is deliberately no student-edit endpoint yet — a wrong
  space/project at registration is caught by the SnapLogic
  project-existence check; changing it later needs a new registration.
- **Auth**: Cognito (admin-created users; groups `admin`/`mentor`/`student`)
  + API Gateway JWT authorizer; the Lambda re-checks source IP and enforces
  the role matrix (mentors get 403 on /v1/syncs; `student` is read-only —
  exercise list + input files yes, but 403 on every action and on
  config/job-polling/`GET /v1/exercises/{slug}`, the last because it carries
  notes.md, i.e. instructor hints). IP allowlist also runs at the edge via a
  CloudFront Function.
  **Student self-scoping (July 2026)**: a `student` sees only their OWN grades,
  not the roster. `GET /v1/students` returns only the card whose stored email
  matches the caller's email claim (`_own_student_slug` / `_is_student_only` in
  `api.py`); `GET /v1/students/{slug}` and `.../reports` 403 on any slug that
  isn't the caller's own card. Admins/mentors are never scoped. The email is
  the link between the Cognito login and the card — set when the login is
  created (see below), stored lowercased on the card, compared case-folded
  here. The SPA confines student-only users to `/students/<own-slug>` (a
  My Grades / Exercises / Manager top-bar nav only; every other route
  redirects there), resolving the slug from the scoped
  `GET /v1/students`; the backend is the real boundary, the UI is cosmetic.
  **Student logins (July 2026)** are app-created, never console-created: an
  optional email on POST /v1/students makes the API `AdminCreateUser` the
  student into the `student` group — Cognito emails the temporary password
  and the hosted UI forces a change on first sign-in, so the API never
  handles a password. Registration with an email fails as a unit (Cognito
  refusal rolls the card put back). The email lives on the STUDENT card and
  the worker's card refresh carries it forward; the admin-only student hard
  delete also removes the Cognito login, so a purged student can't keep
  signing in.
  **Self-service settings + MFA (July 2026)**: the pool is `mfa_configuration =
  "OPTIONAL"` with software-token MFA on. With OPTIONAL MFA the hosted UI never
  prompts anyone to enroll a TOTP authenticator (that auto-prompt only fires when
  MFA is `"ON"`), and no admin API can register a TOTP device for another user
  (associate/verify needs the shared secret + a live code) — so the SPA drives
  enrollment itself from the in-app **Manager** page (`pages/Manager`,
  `src/cognito.ts`; originally a Settings modal, made a top-bar tab July 2026
  to mirror the classic console's Designer/Manager/Dashboard header):
  associate → QR/secret → verify → set-preference, all via the
  Cognito user-pools JSON API authorized by the signed-in user's **access token**
  (plain fetch, no AWS SDK/SigV4). The same page changes the password and sets a
  **display name** (the `name` attribute — Cognito usernames are immutable, so
  there's no true rename; login email is unchanged). All of this requires the
  `aws.cognito.signin.user.admin` scope on the SPA app client + `oidcConfig`; the
  scope only lands in freshly issued tokens, so after adding it every existing
  session must sign out and back in once (the page reads the access-token
  `scope` claim and shows a notice if it's absent). Flip the pool to `"ON"` to
  require a second factor for everyone (then the hosted UI handles enrollment and
  the in-app flow is unnecessary).

## Batch grading for full runs (July 2026)

To cut AI cost, a **full "grade all exercises" run** judges every exercise
through Anthropic's **Message Batches API**, billed at **50%** of standard
token rates. The batch is asynchronous (usually minutes, max 24h), which
doesn't fit the synchronous worker (one invocation, 15-min cap), so a full
grade is a **two-phase job on the existing SQS + worker** — no new AWS
services:

- **submit** (`worker._submit_grade_batch_job` → `runner.submit_grade_batch`):
  materialize + `grade.cmd_plan` (hard gates for all exercises) → build one
  batch from the `ready_for_ai` bundles (`AIJudge.build_batch_requests`,
  `custom_id = slug`) → `batches.create` → stash the plan scratch
  (`manifest.json` + per-slug `ai_context.json`/`evaluation.json`) in S3 under
  `jobs/<job_id>/` (`S3Store.upload_scratch`) → set the JOB to
  `batch_processing` + `batch_id` → **re-enqueue a delayed SQS message to our
  own queue** (`_enqueue_delayed`, `phase: "collect"`). If the plan found
  nothing to judge (all deterministic), the report is rendered synchronously
  and there's no batch.
- **collect** (`worker._process_grade_collect` → `runner.collect_grade_batch`):
  the delayed message fires → `batches.retrieve`. Still processing → refresh
  the lock TTL, bump `poll_attempts`, re-enqueue (bounded by
  `_BATCH_MAX_POLLS`). Ended → restore the scratch, write each result's
  `evaluation.json`, `grade.cmd_report` (full), one **synchronous** (full-price,
  negligible) `overall_summary` call, upload the report + write the REPORT /
  STUDENT rows (shared `_finalize_grade_rows`), release the lock, delete the S3
  scratch.

Why this shape:

- **The wait is on Anthropic's side, not the worker.** submit and collect are
  each a few seconds; between them the delayed poll message is invisible in
  SQS, so the single-concurrency worker is free to grade other students.
- **Scratch path invariance.** Manifest entries store paths anchored under
  `TMP_DIR` (absolute `/tmp/...` on Lambda — `TMP_DIR` isn't under `REPO_ROOT`,
  so `grade._rel_to_repo` falls back to absolute). Restoring the scratch to the
  same `/tmp` path makes `grade._resolve_manifest_path` resolve unchanged, so
  `cmd_report` runs as if it were one invocation.
- **Lock across the gap.** The per-student `LOCK#grade#<slug>` (TTL 30 min) is
  held from submit through collect and its TTL refreshed on each poll
  (`_refresh_lock_ttl`), so a batch longer than 30 min can't lose its lock and
  let a concurrent grade start. The lock is released only on a terminal outcome.
- **Retry is safe.** Re-reading a *finished* batch costs nothing, so a
  transient collect error re-enqueues (bounded) rather than dead-lettering a
  grade whose paid batch already succeeded — unlike the general "never
  auto-retry a paid job" DLQ rule, which still governs the sync path and submit.
- **Cost accounting.** `JudgeUsage.batch` halves `est_cost_usd`; the per-exercise
  batch usage carries it, the synchronous Overall call is recorded separately at
  full price (`GradeRunResult.overall_usage`, summed by `_finalize_grade_rows`).

Only the **full run** batches; a subset selection or single-task **Regrade**
stays on the synchronous `run_grade` path (instant). The API routes on scope
(`no task/tasks → batch`); it also stamps a cosmetic `mode` on the JOB row.
Infra delta: the worker Lambda gains `sqs:SendMessage` on its own queue, a
scoped `s3:DeleteObject` on `jobs/*`, and a `QUEUE_URL` env var.

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
  is the pipeline's output (typically JSON). Used by sync to capture
  solution responses for `triggered_task` exercises.

The Public-API `/catalog/...` endpoint is a paid feature; we don't use it.

## Pipeline execution for triggered-task exercises

Two execution models, selected per-exercise via `task_type` in
`task.json`:

- `csv_writer` (Task 01 etc.) — pipeline runs server-side at some point
  earlier; we fetch the already-produced CSV from SLDB for both
  solution and student. No execution at evaluation time.
- `triggered_task` (Task 02 onward) — pipeline is exposed as a
  SnapLogic Triggered Task. sync invokes the solution task once per
  scenario in `task.json.requests` and saves each JSON response to
  `expected/<scenario_name>.json`. /grade does the same against the
  student's task. This DOES execute the pipeline server-side and
  counts against execution quota; the sync cache (sidecar keyed off
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
The evaluator layer is unchanged by the cloud pivot — it just reads the
materialized tree.

**Authoring happens in the web UI** (Add New Exercise / Edit, admin
only): description.md + notes.md text, input-file uploads, and a task
type selector whose structured config (`task_config` on the EXERCISE
row) replaces the hand-written task.json — the worker synthesizes
task.json from it at sync time ("auto" = single-output file_writer,
where sync detects the lone writer as always). Dropping a folder in git
still works as a **create-only fallback**: the next sync seeds its
authored files into S3, after which the UI owns the content (git edits
no longer propagate — S3 wins).

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
  sync deletes any other files in `expected/` during reconcile, so
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
  by `_proposed_path` on the next sync. The genuinely env-specific caches
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
