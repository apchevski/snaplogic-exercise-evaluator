# SnapLogic Exercise Evaluator

Automated grading for SnapLogic training exercises. AI-driven judgment — designed
for exercises that admit many correct solutions, so judgment comes from a model
rather than a rubric.

> **⚠️ Architecture transition (June 2026):** the project has moved to a fully
> cloud-hosted grading flow — mentors click a **Grade** button on a web dashboard;
> grading (deterministic hard gates + Claude API judgment, Sonnet 4.6) runs in
> AWS, with nothing installed locally. The platform code (backend Lambdas,
> Terraform, React SPA, CI/CD) is **implemented**; see
> [Cloud grading platform](#cloud-grading-platform) for the deployment steps
> that remain. The local `/grade` Claude Code skill was removed; `/prep`
> (exercise maintenance) remains available locally as the dev fallback until
> cloud sync is verified.

## What it does

Mentors and admins log into a VPN-restricted web dashboard (styled after the
classic SnapLogic Dashboard: navy panel headers, sortable/paginated data
tables, tabbed sub-nav):

- **Grade** (mentor or admin): click **Grade** on a student's row. A scope
  picker opens with every active exercise preselected — keep them all for a
  full run, or check just the exercises you want. A job queues, a worker
  Lambda runs the deterministic hard gates against SnapLogic, sends each
  surviving exercise to Claude (Sonnet 4.6, ~$0.95 per full run), renders
  the report, and the row refreshes live with points and per-task detail. A
  full run also refreshes the AI Overall summary; a subset run only replaces
  the selected exercises' results.
- **Add a student** (mentor or admin): click **Add Student** in the toolbar.
  The dialog takes the student's name plus the SnapLogic **project space**
  (prefilled with the configured default, `SNAPLOGIC_STUDENT_PROJECT_SPACE`)
  and optionally a **project** name for when the project isn't named exactly
  after the student. Both are stored on the student and dictate where every
  later grading run looks for their pipelines. The API first verifies the
  project exists at that location (a typo gets a clear "no project named …"
  error instead of a card that every grading run would fail on), then
  registers the student with zero exercises graded (and $0 spent) — grading
  starts later from the row's **Grade** button. An optional **student email**
  additionally creates a read-only web login for the student: Cognito emails
  them a temporary password, they change it on first sign-in, and from then
  on they can watch their grades (see the `student` role below).
- **Student sign-in** (read-only): users in the `student` Cognito group see
  the same Students and Exercises pages mentors see — grades, summaries,
  task descriptions, downloadable input files — but every action is gone
  (and 403s server-side): no grading, no registering, no report edits, no
  instructor notes.
- **Regrade one exercise** (mentor or admin): on a student's detail page,
  every task card has a **Regrade** button that re-runs just that exercise
  (one Claude call instead of one per exercise — faster and cheaper than a
  full run). The result is merged into the student's existing report; all
  other task results, and the Overall summary, are left untouched.
- **Not-graded visibility**: exercises a student has never been graded on
  (registered-only students, or exercises added after their last run) show
  as **not graded** cards on the detail page — each with its own **Grade**
  button — plus a **Not Graded** count column on the dashboard (next to
  Pass/Fail/Missing) and a badge in the grade summary.
- **Edit report text** (mentor or admin): next to each task card's Regrade
  button — and beside the Overall summary — a pencil button opens an inline
  editor to rewrite the AI's summary text. Edits are saved into the stored
  report in place ($0 — no re-grade); verdicts, points, and deductions are
  untouched. Regrading a task later replaces its edited summary with fresh
  AI text.
- **Sync** (admin only): click Sync on an exercise to refresh its solution
  cache + expected outputs from SnapLogic into S3 ($0 — no AI involved).
- **Remove a student** (admin only): a red **Remove** button on the
  student's row opens a confirmation dialog, then permanently deletes the
  student from AWS — dashboard card, full report history (every S3 version),
  their grading-job records, and the web login their registration created
  (if any). Their SnapLogic project is untouched.
- **Delete an exercise** (admin only): a red **Delete** button next to
  Archive opens a confirmation dialog, then permanently deletes the exercise
  from AWS — authored content, sync artifacts and input files (every S3
  version) plus its DynamoDB and job records — and scrubs its result out of
  every student's live report (points, counts and totals are recalculated;
  older report versions keep their history). Archive remains the reversible
  alternative. Exercises that still ship in the container image keep a
  minimal tombstone row so the image copy can't resurrect them; re-creating
  the same folder name later replaces the tombstone.
- **Exercises** (mentor or admin): the exercise list shows sync status per
  task; click a task name to expand its full description (rendered from the
  exercise's `description.md`). Exercises that ship input data (zips, CSVs
  under `exercises/<slug>/resources/`) show a **Files** column — click a
  file to download it (served via a short-lived presigned S3 URL).

Exercise *authoring* stays in git (description.md, notes.md, rules); the
`/prep` Claude Code skill still works locally as a dev fallback:

```
/prep                          # reconcile every exercise folder against SnapLogic
python -m evaluator run <student>   # local twin of the cloud grade job
                                    # (needs ANTHROPIC_API_KEY; costs real money)
```

## Cloud grading platform

```
Browser (VPN/office IPs only)
  ├─► CloudFront ── CF Function (IP allowlist) ──► S3 (React SPA, frontend/)
  └─► API Gateway HTTP API /v1 ── JWT authorizer (Cognito) on every route
        ├─ GET  students / reports / exercises / files  (any role, students too)
        ├─ GET  /v1/config, job status, authored content       (mentor or admin)
        ├─ POST /v1/students {student, space?, project?, email?} — register, no
        │        grading; 400 unless the SnapLogic project exists; the stored
        │        space/project dictate later grading runs; an email creates a
        │        read-only Cognito login for the student      (mentor or admin)
        ├─ POST /v1/gradings {student, task?|tasks?}          (mentor or admin)
        ├─ PATCH /v1/students/{slug}/report — edit AI text    (mentor or admin)
        ├─ POST /v1/syncs {slug?}                             (admin only)
        ├─ POST/PUT /v1/exercises — create / edit / archive   (admin only)
        ├─ DELETE /v1/students/{slug} — purge everything      (admin only)
        └─ DELETE /v1/exercises/{slug} — purge + report scrub (admin only)
                  │ JOB item (DynamoDB) + SQS message
                  ▼
SQS ──► Worker Lambda (container image, 15-min cap, concurrency 1, DLQ no-retry)
          ├─ authored + generated exercise content from S3 (image = seed only)
          ├─ SnapLogic REST (GET-only, creds from Secrets Manager)
          ├─ grade: hard gates → Claude (Sonnet 4.6, structured outputs,
          │         prompt-cached rules) → report.md/.json → S3 + DynamoDB
          └─ sync:  evaluator.sync sync → artifacts to S3 ($0 AI)
```

| Piece | Where |
|---|---|
| Headless judge / runner / store | `evaluator/ai_judge.py`, `evaluator/runner.py`, `evaluator/store.py` |
| API + worker Lambdas | `backend/src/` (tests in `backend/tests/`, all moto/stub — $0) |
| Structured-outputs schemas | `schemas/` |
| Lambda container image | `Dockerfile` (one image, two CMDs) |
| Terraform (12 AWS services, ≈$0.50–0.70/mo) | `infra/` (bootstrap + environments/production + modules) |
| React SPA | `frontend/` (Vite + TS, Cognito Hosted UI + PKCE; unit tests via `npm test` — vitest) |
| CI/CD (GitHub OIDC, no stored keys) | `.github/workflows/` |

**Roles** (Cognito groups; the API enforces, the UI only hides buttons):
admins sync + grade + view; mentors grade + view; students view only (no
grading, no edits, no instructor notes). Admin/mentor users are invite-only
(admin-created in the Cognito console — no self-signup); student logins are
created by the app itself when a registration includes an email — never add
someone to the `student` group by hand alongside an admin/mentor invite.
**MFA** is optional TOTP (authenticator app): the pool is `mfa_configuration =
"OPTIONAL"` with software-token MFA on. With OPTIONAL MFA the hosted UI does
**not** prompt anyone to enroll, and you can't pre-register someone's
authenticator from the console — so users enroll themselves from the in-app
**Account menu → Settings → Two-factor authentication** (scan the QR, enter a
code, done; next sign-in then asks for a code). That Settings dialog also lets
users change their password and set a display name, and it relies on the
`aws.cognito.signin.user.admin` scope granted to the SPA app client — after
deploying that scope, existing sessions must sign out and back in once before
Settings works. Set the pool to `"ON"` to require a second factor for everyone
(then the hosted UI drives enrollment at sign-in and the in-app flow isn't
needed).

### Deploying (one-time, in order)

1. `infra/bootstrap`: `terraform init && terraform apply` once to create the TF
   state bucket.
2. `infra/environments/production`: `terraform init`, then apply ECR first
   (`-target=module.data -target=module.secrets -target=module.ecr`). The
   Lambdas are container images and need one to exist, so build + push once by
   hand: `docker build -f Dockerfile -t <ecr-url>:latest . && docker push …`,
   then run a full `terraform apply`.
3. Put the secret value (SnapLogic creds + Anthropic key) into Secrets
   Manager — the exact CLI command is in `infra/modules/secrets-manager/main.tf`.
4. Create users in the Cognito console and add them to `admin` / `mentor`
   (the third group, `student`, is populated by the app — see Roles above).
5. Fill the blank values in `.github/deploy.vars` from `terraform output` (the
   deploy workflows load that file — no GitHub Variables to set by hand; there
   are no CI secrets, auth is OIDC). Commit, then deploy: push to `main`
   (auto-deploys via path filters) or run a workflow manually from the Actions
   tab / `gh workflow run` against any branch. CI takes over (image → Lambdas,
   SPA → S3 + CloudFront).
6. Click **Sync All Exercises** (admin) once. Besides generating artifacts,
   this seeds every image-shipped exercise's authored files
   (description/notes/resources) into S3 — the canonical exercise store —
   after which the UI owns exercise content end to end.

**One-time GitHub setup for gated infra applies:** `deploy-infra` runs
`terraform plan` on every PR/push and uploads the plan, but the `apply` job is
pinned to a `production` GitHub **Environment**. Create it once under
**Settings → Environments → New environment → `production`** and add yourself
under **Required reviewers** (leave *Prevent self-review* unchecked so a solo
operator can approve their own run). After that, every push to `main` that
touches `infra/**` plans automatically and then **pauses for approval** — open
the run, read the plan in the job summary, and click **Approve** to apply the
exact plan you reviewed (or **Reject** to cancel). If remote state drifted
between plan and approval, terraform refuses the stale plan — just re-run.

## Verdicts and points

Every exercise resolves to exactly one of three verdicts, with a 0–10
point score:

| Verdict   | Meaning                                              | Points  |
|-----------|------------------------------------------------------|---------|
| **PASS**  | Every hard gate passed (output matches the solution) | `10 − Σ deductions`, floor `0`. Verdict stays PASS even if deductions exceed 10. |
| **FAIL** (output-mismatch)  | `output_match` or `triggered_task_responses_match` failed — output is wrong | `10 − Σ deductions`, floor `0` — AI judges pipeline structure for partial credit |
| **FAIL** (procedural)       | Pipeline name wrong (deliverable is there but doesn't follow the naming convention) | `0` (AI not invoked) |
| **MISSING** | Student didn't submit a runnable deliverable: no matching pipeline, OR no output uploaded to SLDB (file_writer), OR no Triggered Task with the convention name (triggered_task) | `—` (not graded; counts as `0/10` toward the per-student total) |

**Why FAIL has two flavors**: a student whose pipeline is structurally
correct except for one misspelled string literal should not be ranked
alongside a student who submitted an empty pipeline. Output-mismatch
FAILs (`output_match`, `triggered_task_responses_match`) still go
to the AI for partial credit — the verdict stays FAIL because the
output is wrong, but points reflect how close the pipeline is to a
correct solution. Procedural FAILs (name mismatch) stay at 0 because
there's nothing partial to credit.

**Why "deliverable not submitted" is MISSING, not FAIL**: a submission
that doesn't include a runnable deliverable can't be graded at all —
the student didn't submit anything to evaluate. This covers both
file_writer (no output file in SLDB → student never ran it) and
triggered_task (no Triggered Task with the convention name → student
didn't create the artifact that lets the task be invoked). MISSING
exercises are not AI-judged (there's nothing to judge) but they still
count as `0/10` toward the per-student total — the denominator is
always `(total exercises) × 10`, regardless of how many were
actually graded, so a student who skipped half the exercises sees
that reflected in their total.

Deductions for every PASS or output-mismatch FAIL come from rules with
**explicit point values** written into
`exercises/general_evaluation_rules.md` (universal SnapLogic best
practices) and per-exercise `exercises/<slug>/notes.md` (task-specific
guidance). The AI judge applies the value the rule states (`-2`, `-1`,
or *mention only*) — it never invents a deduction value. This is what
guarantees the same mistake costs the same points for every student,
every time.

If the AI sees something off that no rule covers with explicit points,
it surfaces it under **Notes** in the report — no points deducted.

### `/prep` — keep exercise folders in sync with SnapLogic

The `prep` skill walks `exercises/`, reads the canonical pipeline name from each
folder's `description.md` H1 heading, looks the pipeline up in the solution
project space, and reconciles local files against the live SnapLogic state:

- Auto-creates `task.json` for **single-output file_writer** exercises (the lone
  writer filename is derived from the binary-write snap).
- Asks the operator to hand-write `task.json` for **multi-output file_writer**
  exercises (lists every required output under `output_filenames`) and for
  **triggered_task** exercises (the script can't derive the Triggered Task name
  or scenarios).
- Detects pipeline renames, writer-filename renames, and stale solution caches;
  rewrites `solution.json`, `solution.cache.json`, and `expected/` to match.
- Prunes obsolete files in `expected/`, keeping only the current outputs.

Run `/prep` whenever you add a new exercise folder or edit a solution pipeline.
`/grade` refuses to grade folders that are not fully prepped.

`/prep --task <slug>` surveys and reconciles just one folder.

### `/grade` — grade a student

The `grade` skill then:

1. Resolves the student's project location from `.env` defaults (org +
   `SNAPLOGIC_STUDENT_PROJECT_SPACE` + student name → project path).
2. Discovers every registered exercise from `exercises/*/task.json`.
3. For each exercise, runs the deterministic Python evaluator which:
   - Fetches both the solution pipeline and the student's pipeline (GET-only).
   - Applies hard gates: pipeline name match (dash-tolerant) and **either**
     output file match (file_writer) **or** Triggered Task name match plus
     per-scenario JSON response match (triggered_task).
   - On hard-gate fail → writes a complete `evaluation.json` and stops.
   - On hard-gate pass → writes an `ai_context.json` bundle (description,
     instructor notes, topologically-sorted snap flows, both raw pipeline
     JSONs, plus per-scenario request/response pairs for triggered_task) and
     emits `READY_FOR_AI_REVIEW`.
4. The skill picks up from there: reads the context bundle, judges
   structural differences in-conversation, and writes the final
   `evaluation.json`. **The AI step runs inside your Claude Code session
   — no API calls.**
5. Composes `grades/<student>/report.md` (human-readable) and
   `grades/<student>/report.json` (structured mirror for downstream tooling /
   future UI) aggregating every exercise. Scratch artifacts under
   `.tmp/grades/<student>/` are deleted at the end of the run — only the two
   `report.*` files persist.

`/grade <student> --task <slug>` re-grades a single exercise and rewrites only
that task's section in the existing `report.md` in place (the date is left
untouched). The matching task entry in `report.json` is updated in lockstep,
and the JSON `counts` / `points_earned` / `points_possible` are recomputed from
the merged task list. The skill then **refreshes the `## Overall` paragraph and
reconciles the markdown header totals** so the report reflects the just-graded
task — every grading run, full or single-task, leaves a current Overall rather
than a stale one. (The other tasks' sections are not re-evaluated.)

### Pipeline-name matching: dash-tolerant for pipelines, strict for Triggered Tasks

The SnapLogic Designer freely substitutes hyphen-minus (`-`), en dash (`–`),
and em dash (`—`) in pipeline names. The pipeline-name hard gate treats all
three glyphs as equal, so `Task 03 – Join Employee Records` (en dash) matches
`Task 03 - Join Employee Records` (hyphen).

Triggered Task names, by contrast, are matched **strictly** (byte-for-byte) —
the URL is computed from the exact string and any normalization there would
silently route to the wrong task.

## Project layout

```
.
├── README.md
├── SOLUTION_OVERVIEW.md        # one-read map of the whole solution (for new contributors, human or AI)
├── CHANGELOG.md
├── LICENSE
├── requirements.txt
├── docker-compose.yml          # local dev: api/worker (Lambda RIE) + cli services
├── .env.example                # template; copy to .env and fill in
├── .claude/
│   ├── CLAUDE.md               # operating rules (auto-loaded by Claude Code)
│   ├── architecture.md         # design notes
│   ├── project.md              # project framing
│   ├── snaplogic_api_findings.md  # REST API discoveries / gotchas
│   ├── settings.json           # Claude Code project settings
│   ├── conventions/            # one file per project-wide or skill-scoped rule
│   └── skills/
│       └── prep/SKILL.md       # the /prep slash command
├── exercises/
│   ├── general_evaluation_rules.md
│   ├── task_01_generate_csv_report/   # file_writer example
│   │   ├── task.json           # COMMITTED: intent (output_filename(s)); solution_pipeline_path auto-rewritten by /prep
│   │   ├── description.md      # the student-facing prompt (H1 = canonical pipeline name)
│   │   ├── notes.md            # instructor hints fed to the AI judge
│   │   ├── resources/          # student-facing input data (e.g. Task1.zip) — downloadable from the Exercises page
│   │   ├── solution.json       # cached solution pipeline JSON (gitignored; fetched by /prep)
│   │   ├── solution.cache.json # sidecar: signature + snode_id for cache invalidation (gitignored)
│   │   └── expected/           # golden output file(s) (gitignored; auto-fetched by /prep)
│   └── task_02_calculator/     # triggered_task example
│       ├── task.json           # COMMITTED: intent (triggered_task_name + requests[]); path auto-rewritten by /prep
│       ├── description.md
│       ├── notes.md
│       ├── solution.json
│       ├── solution.cache.json
│       └── expected/           # one <scenario>.json per request in task.json
├── grades/                     # persistent per-student report.md + report.json (local runs; cloud keeps them in S3)
├── evaluator/
│   ├── __init__.py
│   ├── __main__.py             # `python -m evaluator ...` (incl. the `run` subcommand)
│   ├── config.py               # env loading; EVALUATOR_*_DIR overrides for Lambda
│   ├── snaplogic_client.py     # GET-only SnapLogic REST client
│   ├── pipeline_fetch.py       # pipeline + SLDB file retrieval, topo sort, triggered-task probes
│   ├── name_match.py           # dash-tolerant pipeline-name comparison
│   ├── hard_gates.py           # name + output equality checks (CSV/XLSX or per-scenario JSON)
│   ├── tasks.py                # task.json discovery + TaskConfig (file_writer | triggered_task)
│   ├── evaluate.py             # per-task evaluator (no LLM call)
│   ├── sync.py                 # /prep skill orchestrator + CLI (also runs inside cloud sync jobs)
│   ├── grade.py                # plan/report orchestrator + CLI
│   ├── ai_judge.py             # headless Claude judge (Sonnet 4.6, structured outputs)
│   ├── runner.py               # in-process grade run: gates → judge → report → Overall
│   └── store.py                # LocalStore / S3Store artifact + report I/O
├── backend/
│   ├── src/                    # api.py (Powertools router) + worker.py (SQS consumer) + common.py
│   └── tests/                  # pytest: moto AWS + stubbed Claude — $0, run on every PR (deploy-backend `test` job)
├── schemas/                    # structured-outputs JSON schemas for the judge
├── Dockerfile                 # cloud image (api + worker share it; CMD differs)
├── infra/                      # Terraform: bootstrap (state bucket) + environments/production + modules/
├── frontend/                   # React SPA (Vite + TS): login, dashboard, student detail, exercises
├── .github/workflows/          # deploy-backend (test→build→deploy), deploy-frontend (test→build→deploy), deploy-infra (validate→plan→apply); gates run on PRs, deploy on main
└── .tmp/                       # scratch space during a grading run; cleaned out per student
```

## Setup

> The recommended path is **[Running in Docker](#running-in-docker-no-local-python)** —
> no Python install at all. The venv setup below is the optional escape hatch
> (and is still how `.env` gets created — Docker needs that step too).

```powershell
# from repo root

# 1. Credentials (required for BOTH Docker and venv)
Copy-Item .env.example .env
notepad .env   # set SNAPLOGIC_* values

# 2. Local Python (only if you are NOT using Docker)
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Required env vars (see `.env.example`):

| Variable                            | Purpose                                                              |
|-------------------------------------|----------------------------------------------------------------------|
| `SNAPLOGIC_BASE_URL`                | e.g. `https://elastic.snaplogic.com`                                 |
| `SNAPLOGIC_ADMIN_USERNAME`          | Admin user with read access to both project spaces                   |
| `SNAPLOGIC_ADMIN_PASSWORD`          | Admin password                                                       |
| `SNAPLOGIC_ORG_NAME`                | Top-level org (used as the first path segment in every lookup)       |
| `SNAPLOGIC_SOLUTION_PROJECT_SPACE`  | Project space holding the **solution** pipelines                     |
| `SNAPLOGIC_SOLUTION_PROJECT`        | Project (within the solution space) holding the solution pipelines   |
| `SNAPLOGIC_STUDENT_PROJECT_SPACE`   | **Default** project space for students (default `IWC_Support`) — prefills the Add Student dialog; the per-student space stored at registration wins |

> Migration note: `SNAPLOGIC_PROJECT_SPACE_NAME` / `SNAPLOGIC_PROJECT_NAME` were
> renamed to `SNAPLOGIC_SOLUTION_PROJECT_SPACE` / `SNAPLOGIC_SOLUTION_PROJECT`
> to make it explicit that they point at the solution, not the student.

## Running

**Primary entry point — slash commands in Claude Code:**

```
/prep                                          # reconcile all exercise folders
/prep --task task_02_calculator                # reconcile one folder
/grade Gabriela Shurbeska                      # grade one student
/grade --space Test_Antonio "Some Student"     # override the student project space
/grade "Gabriela Shurbeska" --task task_01_generate_csv_report   # re-grade one task
```

**Lower-level — running the Python evaluator directly for one exercise:**

```powershell
.\.venv\Scripts\Activate.ps1
python -m evaluator task_01_generate_csv_report `
  --student "Interworks-Partner/IWC_Support/Gabriela Shurbeska/Task 01 – Generate CSV Report"
```

This runs only the deterministic part. The student name is auto-derived
from the third segment of `--student` (e.g. "Gabriela Shurbeska"). On
hard-gate fail it writes `.tmp/grades/<student>/<task>/evaluation.json`
directly. On hard-gate pass it writes
`.tmp/grades/<student>/<task>/ai_context.json` and exits 0 with
`READY_FOR_AI_REVIEW` — you'd then need the `/grade` skill (or another
caller) to finish the AI judgment.

The solution pipeline JSON is cached at `exercises/<task>/solution.json`
(committed to the repo) with a sidecar `solution.cache.json` recording
the SnapLogic asset's modified-at timestamp. A run only refetches the
body when the timestamp changes — so back-to-back grading of multiple
students hits the cache. To force a refresh of a solution and its
expected outputs, run `/prep --task <slug>` (or call
`python -m evaluator.sync sync --slug <slug>`).

Flags:
- `--student-name <name>` — override the auto-derived student name
  (used in the output path).

The `/prep` and `/grade` orchestrators are exposed as their own subcommands:
`python -m evaluator.sync {survey,sync}` and
`python -m evaluator.grade {plan,report,sync-overall}`. `sync-overall` is a
small helper that copies the rendered `## Overall` paragraph from `report.md`
into `overall_summary` inside `report.json`. (The `/grade` skill that drove
these commands has been removed — grading is moving to the cloud; the CLI
entry points remain and will be reused by the cloud grading worker.)

## Running in Docker (no local Python)

`docker-compose.yml` defines three services, all built from `Dockerfile` —
the same image that runs in AWS:

| Service | What it does | Port |
|---------|-------------|------|
| `api` | API Lambda via RIE — HTTP API proxy events | 9000 |
| `worker` | Worker Lambda via RIE — SQS-style invocations | 9001 |
| `cli` | evaluator CLI — `sync` / `run`; bind-mounts repo dirs | — |

### Prerequisites

- Docker (Desktop on Windows/macOS, Engine on Linux), running.
- A filled-in `.env` at the repo root (see [Setup](#setup)).
- Build the image once: `docker compose build` (re-run when `evaluator/`,
  `backend/`, `schemas/`, or `requirements.txt` changes).

### CLI (sync, local run)

The `cli` service overrides the Lambda entrypoint to run Python directly.
Bind mounts keep all writes in your workspace:

```powershell
docker compose run --rm -T cli python -m evaluator.sync survey
docker compose run --rm -T cli python -m evaluator.sync sync --slug task_02_calculator
docker compose run --rm -T cli python -m evaluator run "Gabriela Shurbeska"  # costs real money
docker compose run --rm -T cli python -m evaluator.ui --no-open
```

> **Escape hatch (no Docker):** substitute `.venv/Scripts/python.exe` (or `python`)
> for `docker compose run --rm -T cli python` in any command above, after the
> venv [Setup](#setup).

### API / worker (local Lambda testing)

The RIE is bundled in the base image — no extra setup:

```powershell
# Start the API Lambda
docker compose up api

# Invoke it (from another shell; PowerShell needs backtick line-continuation)
curl -sX POST http://localhost:9000/2015-03-31/functions/function/invocations `
  -H "Content-Type: application/json" `
  -d '{"version":"2.0","routeKey":"GET /v1/health","rawPath":"/v1/health","headers":{},"requestContext":{"http":{"method":"GET","path":"/v1/health"}},"isBase64Encoded":false}'

# Run the worker Lambda once
docker compose run --rm worker
```

## Grade dashboard (browser UI)

`/grade` rebuilds `frontend/dist/index.html` silently at the end of every run (both full
and single-task mode), so the dashboard stays in sync with `grades/`
automatically. Open the file once in your browser and refresh after each
grade run — no extra command needed.

To explicitly rebuild the page in Docker (headless — open `frontend/dist/index.html`
yourself afterward):

```powershell
docker compose run --rm -T cli python -m evaluator.ui --no-open
```

(Or, with the venv escape hatch, `.\.venv\Scripts\python.exe -m evaluator.ui`
also opens it in your default browser.)

This walks every `grades/<student>/report.json` and generates a single
self-contained `frontend/dist/index.html` with the data embedded inline (no HTTP server,
no `fetch` calls). Features:

- Search by student name; filter by project space.
- Sort by total points (default), pass count, name, or grading date.
- Per-student card with a colored **Total: X/Y pts** badge (green/amber/red by
  ratio) plus verdict counts (pass / fail / missing / needs sync).
- Overall summary paragraph (from `## Overall` in `report.md`).
- Collapsible per-task accordion showing each task's verdict, `points/10`
  pill, summary, failing gate (if any), and the differences list split into
  **Deductions** (with `−N pts` chip and the `rule_source`) and **Notes**
  (mention-only). Bonus-question answers are surfaced inline.

Pass `--no-open` to build the page without opening it (this is what
`/grade`'s auto-rebuild uses internally). The `frontend/dist/` folder is gitignored —
it's purely derived from `grades/`.

Exit codes:
- `0` — hard gates passed (AI step pending, or all gates passed)
- `1` — procedural hard gate failed (pipeline name mismatch)
- `2` — bad CLI args / missing required env var / unknown task slug
- `4` — deliverable not submitted (`output_present` 404 OR `triggered_task_exists` missing) — orchestrator treats as MISSING

## Managing exercises

**S3 is the source of truth for exercise content.** Exercises are created,
edited and archived from the web UI; the `exercises/` folders in this repo
are a *seed* — anything shipped there graduates into S3 on its next sync
(additively; an S3 copy is never overwritten by the image), and from then on
the UI owns it. Durability comes from the AWS side, not from git: bucket
versioning + DynamoDB point-in-time recovery + `prevent_destroy` guards, plus
a nightly one-way snapshot into `exercises-backup/` in this repo
(`backup-exercises.yml`; restore = `aws s3 sync exercises-backup/
s3://<data-bucket>/exercises/`). Never author into `exercises-backup/` — it's
an export, not an input.

### Creating and editing (web UI, admin only)

Click **Add New Exercise** (next to *Sync All Exercises*), or **Edit** on any
row. The dialog takes:

- **Exercise Name** (required) — the human-readable pipeline name (e.g.
  *Task 07 – Router Basics*). Sync looks the solution pipeline up by it, and on
  create the exercise's folder id is derived from it automatically — there's no
  slug to type. Stored as the H1 heading of `description.md` behind the scenes.
- **Description** (required) — the student-facing prompt body (Markdown); no
  need to repeat the name as a heading, the dialog adds it for you.
- **AI Guidance** (optional) — instructor hints fed to the AI judge.
- **Task type** — this replaces the hand-written `task.json`:
  - *File writer, single output* (the default "auto"): nothing to fill in;
    sync detects the pipeline's lone writer snap and generates everything.
  - *File writer, multiple/custom outputs*: list the output filenames and
    pick the comparison mode (`exact` vs `columns_only` for
    non-deterministic outputs).
  - *Triggered task*: the Triggered Task name (defaults to
    `<pipeline name> Task`, the strict convention) and one row per request
    scenario — a snake_case name plus `param=value; param2=value2` pairs.
  The config is stored as structured data; the worker synthesizes
  `task.json` from it (plus the env-derived pipeline path) at sync time.
- **Input files** (optional) — uploaded browser → S3 via presigned URLs;
  students download them from the Exercises page. In edit mode, click an
  existing file to mark it for deletion.

Markdown lands in S3 under `exercises/<slug>/`; the worker overlays that
prefix onto its working tree before every job, so a UI-authored exercise is
indistinguishable from a seeded one. Finish by clicking **Sync** on the row.

**Archive** (admin, per row) soft-deletes an exercise: it stops being
synced, graded and counted toward student totals, and shows greyed-out with
an `archived` badge. Nothing is removed from S3 — **Unarchive** restores it
fully. There is deliberately no hard delete.

### Fallback: authoring in git

If the UI is unavailable you can still drop a folder in the repo — the
pre-pivot flow. On its next sync the folder's authored files are seeded into
S3 and the UI takes over; **content edits in git do NOT propagate after
that** (the S3 copy wins), so treat git authoring as create-only.

1. Create `exercises/<slug>/description.md` — the student-facing prompt. The
   **first H1 heading** is the canonical pipeline name (e.g.
   `# Task 03 – Join Employee Records`); both the solution and the student's
   pipeline must use that name in SnapLogic.
2. Optionally create `exercises/<slug>/notes.md` (instructor hints — fed
   to the AI judge). Put only **task-specific** rules here; the universal
   best-practice rules in `exercises/general_evaluation_rules.md` apply
   automatically. Use `notes.md` to override a universal rule when the
   exercise legitimately requires it.
3. If the exercise hands the student input data (a zip, CSVs, …), put those
   files in `exercises/<slug>/resources/`. They appear automatically as
   download buttons on the web UI's Exercises page — no code or config
   needed. Skip the folder entirely when there are no input files.
4. Run `/prep`.

   - For **single-output file_writer** exercises, `/prep` auto-creates `task.json`
     (with `"output_filename": "<output>.csv"`) and fetches `solution.json` +
     `expected/<output>.csv`. By default the output gate compares the column-name
     set **and** the row multiset. **Column order and row order are both
     ignored** — the student just needs the same column names (same names, same
     count) and the same rows; a different column order is realigned by name
     before rows are compared, so it never fails. Only a missing/extra column or
     differing row data fails. For an exercise whose output is non-deterministic
     (e.g. it calls an API that returns random rows every run), add
     `"output_match_mode": "columns_only"` to `task.json` — the gate then
     compares only the column-name set (any order) and ignores rows, so a correct
     submission still passes. The header reader is format-aware (real `.xlsx`
     from the Excel Formatter, or CSV). Default is `"exact"`; see
     `exercises/task_04_born_on_friday/`.
   - For **multi-output file_writer** exercises (the pipeline writes several files
     and the student must reproduce **all** of them — e.g.
     `exercises/task_05_multiple_flows_one_pipeline/`), `/prep` can't guess which
     writers are deliverables, so you hand-write `task.json` with the full list
     under `output_filenames` and then run `/prep` to fetch every file:

     ```json
     {
       "task_type": "file_writer",
       "solution_pipeline_path": "Org/ProjectSpace/Project/Pipeline Name",
       "output_filenames": ["Report1.csv", "Report2.csv", "Report3.csv"]
     }
     ```

     The output gate compares **every** file (each header + row multiset); the
     exercise PASSes only when all match. `output_match_mode` applies to all of
     them. Use exactly one of `output_filename` / `output_filenames`.

     > **Back-compat:** the `file_writer` task type was originally named
     > `csv_writer`, with keys `output_csv_filename` / `output_csv_filenames`
     > (the first exercises all wrote CSVs). Those old names are still accepted
     > in `task.json` and normalize to the format-neutral ones; the
     > `sync --output-csv` flag is likewise a deprecated alias for
     > `--output-file`. Write new exercises with the `file_writer` /
     > `output_filename(s)` names.
   - For **triggered_task** exercises, `/prep` asks you to hand-write
     `task.json` because the script can't derive the Triggered Task name or
     scenarios. The schema is:

     ```json
     {
       "task_type": "triggered_task",
       "solution_pipeline_path": "Org/ProjectSpace/Project/Pipeline Name",
       "triggered_task_name": "Pipeline Name Task",
       "requests": [
         { "name": "addition",    "params": { "mathOperation": "3+5"  } },
         { "name": "subtraction", "params": { "mathOperation": "10-4" } }
       ]
     }
     ```

     `name` becomes the filename in `expected/` (`addition.json`, …) and the
     scenario label in `ai_context.json`.

No Python edits needed — both skills auto-discover any folder with a
`task.json`.

## Architecture & design notes

New to the project? Start with [SOLUTION_OVERVIEW.md](SOLUTION_OVERVIEW.md) —
a single-file map of the whole solution (architecture diagram, data model,
auth, key flows, CI/CD, and the invariants not to break), written so a new
contributor — human or AI — can onboard from one read.

See [.claude/architecture.md](.claude/architecture.md) and
[.claude/project.md](.claude/project.md) for the design rationale, plus
[.claude/conventions/](.claude/conventions/) for the running list of
project-wide and skill-scoped rules.

## Safety

The SnapLogic client is **GET-only** by construction — `SnapLogicClient`
exposes no `post`/`put`/`delete` method. If you ever need to mutate the
org (e.g., import a pipeline), it must be added explicitly and confirmed
with the project owner first.
