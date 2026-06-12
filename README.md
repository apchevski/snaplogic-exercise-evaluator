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
> cloud prep is verified.

## What it does

Mentors and admins log into a VPN-restricted web dashboard:

- **Grade** (mentor or admin): click Grade on a student card (or type a new
  student's project name). A job queues, a worker Lambda runs the
  deterministic hard gates against SnapLogic, sends each surviving exercise
  to Claude (Sonnet 4.6, ~$0.95 per full run), renders the report, and the
  card refreshes live with points and per-task detail.
- **Prep** (admin only): click Prep on an exercise to refresh its solution
  cache + expected outputs from SnapLogic into S3 ($0 — no AI involved).

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
  ├─► CloudFront ── CF Function (IP allowlist) ──► S3 (React SPA, web/)
  └─► API Gateway HTTP API /v1 ── JWT authorizer (Cognito) on every route
        ├─ GET  students / reports / exercises / job status   (any logged-in user)
        ├─ POST /v1/gradings {student}                        (mentor or admin)
        └─ POST /v1/preps {slug?}                             (admin only)
                  │ JOB item (DynamoDB) + SQS message
                  ▼
SQS ──► Worker Lambda (container image, 15-min cap, concurrency 1, DLQ no-retry)
          ├─ authored content from the image · generated artifacts from S3
          ├─ SnapLogic REST (GET-only, creds from Secrets Manager)
          ├─ grade: hard gates → Claude (Sonnet 4.6, structured outputs,
          │         prompt-cached rules) → report.md/.json → S3 + DynamoDB
          └─ prep:  evaluator.prep sync → artifacts to S3 ($0 AI)
```

| Piece | Where |
|---|---|
| Headless judge / runner / store | `evaluator/ai_judge.py`, `evaluator/runner.py`, `evaluator/store.py` |
| API + worker Lambdas | `backend/src/` (tests in `backend/tests/`, all moto/stub — $0) |
| Structured-outputs schemas | `schemas/` |
| Lambda container image | `Dockerfile.lambda` (one image, two CMDs) |
| Terraform (12 AWS services, ≈$0.50–0.70/mo) | `infra/` (bootstrap + envs/prod + modules) |
| React SPA | `web/` (Vite + TS, Cognito Hosted UI + PKCE) |
| CI/CD (GitHub OIDC, no stored keys) | `.github/workflows/` |

**Roles** (Cognito groups; the API enforces, the UI only hides buttons):
admins prep + grade + view; mentors grade + view. Users are invite-only
(admin-created in the Cognito console — no self-signup).

### Deploying (one-time, in order)

1. `infra/bootstrap`: `terraform apply` once to create the TF state bucket.
2. `infra/envs/prod`: copy `terraform.tfvars.example` → `terraform.tfvars`,
   `terraform init -backend-config="bucket=<state bucket>"`, then apply.
   The first apply creates data/secrets/ECR/Cognito/hosting; Lambdas need an
   image, so build + push once by hand:
   `docker build -f Dockerfile.lambda -t <ecr-url>:latest . && docker push …`,
   then re-apply.
3. Put the secret value (SnapLogic creds + Anthropic key) into Secrets
   Manager — the exact CLI command is in `infra/modules/secrets/main.tf`.
4. Create users in the Cognito console and add them to `admin` / `mentor`.
5. Fill the GitHub repo variables/secrets named in `.github/workflows/*.yml`
   from `terraform output`, push to `main`, and CI takes over (image →
   Lambdas, SPA → S3 + CloudFront, infra plans/applies).
6. Click **Prep** (admin) once so S3 has the generated artifacts, then grade.

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
├── CHANGELOG.md
├── LICENSE
├── requirements.txt
├── Dockerfile                  # deterministic-evaluator image (multi-stage, non-root)
├── docker-compose.yml          # bind-mounts the repo, injects .env
├── .dockerignore
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
│   │   ├── Task1.zip           # student-facing input data
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
│   ├── prep.py                 # /prep skill orchestrator + CLI (also runs inside cloud prep jobs)
│   ├── grade.py                # plan/report orchestrator + CLI
│   ├── ai_judge.py             # headless Claude judge (Sonnet 4.6, structured outputs)
│   ├── runner.py               # in-process grade run: gates → judge → report → Overall
│   └── store.py                # LocalStore / S3Store artifact + report I/O
├── backend/
│   ├── src/                    # api.py (Powertools router) + worker.py (SQS consumer) + common.py
│   ├── tests/                  # pytest: moto AWS + stubbed Claude — $0, run by CI
│   └── requirements.txt
├── schemas/                    # structured-outputs JSON schemas for the judge
├── Dockerfile.lambda           # cloud image (api + worker share it; CMD differs)
├── infra/                      # Terraform: bootstrap (state bucket) + envs/prod + modules/
├── web/                        # React SPA (Vite + TS): login, dashboard, student detail, exercises
├── .github/workflows/          # ci, deploy-backend, deploy-infra, deploy-web
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
| `SNAPLOGIC_STUDENT_PROJECT_SPACE`   | Project space to search when grading a student by name (default `IWC_Support`) |

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
`python -m evaluator.prep sync --slug <slug>`).

Flags:
- `--student-name <name>` — override the auto-derived student name
  (used in the output path).

The `/prep` and `/grade` orchestrators are exposed as their own subcommands:
`python -m evaluator.prep {survey,sync}` and
`python -m evaluator.grade {plan,report,sync-overall}`. `sync-overall` is a
small helper that copies the rendered `## Overall` paragraph from `report.md`
into `overall_summary` inside `report.json`. (The `/grade` skill that drove
these commands has been removed — grading is moving to the cloud; the CLI
entry points remain and will be reused by the cloud grading worker.)

## Running in Docker (no local Python)

Docker is the **primary, fully self-contained way to run the deterministic
evaluator** — only **Docker** and SnapLogic credentials are needed. No Python
install, no venv.

The deterministic `evaluator/` engine runs **in the container**;
`docker-compose.yml` maps the repo into the container, so everything the engine
writes (`exercises/…`, `.tmp/…`, `grades/…`, `ui/index.html`) lands directly in
your workspace. Claude Code's `/prep` slash command drives Docker directly.
(The local AI-judgment grading flow and its `AGENTS.md` / Copilot guides have
been removed — grading judgment is moving to the Claude API in AWS.)

### Prerequisites

- Docker (Desktop on Windows/macOS, Engine on Linux), running.
- A filled-in `.env` at the repo root (see [Setup](#setup)). `docker compose`
  injects it as real environment variables; the file is never copied into the
  image.
- The solution pipelines must exist in your SnapLogic org under the names in
  each exercise's `description.md` H1 heading.
- Build the image once: `docker compose build` (re-run only when `evaluator/`
  changes).

### What a new person does, start to finish

1. `git clone` the repo (the committed `task.json` files carry each exercise's
   intent; `prep` rewrites their env-specific solution path for *your* org).
2. Create `.env`; `docker compose build`.
3. Tell your AI assistant **"prep all exercises"** → it runs `prep sync` in
   Docker (fully deterministic — no judgment). Or run it yourself:
   ```powershell
   docker compose run --rm -T evaluator python -m evaluator.prep sync
   ```
4. Tell your assistant **"grade <student>"** → it runs the three-step flow below.
5. Open `ui/index.html` in your browser.

### The grade flow (container ↔ your AI assistant)

A full grade is a three-step handoff, sharing the bind-mounted `.tmp/` + `grades/`:

1. **Container** — `grade plan <student>` runs the hard gates and writes
   `.tmp/grades/<student>/manifest.json` + an `ai_context.json` per gate-passing
   exercise. The manifest stores **repo-root-relative** paths (e.g.
   `.tmp/grades/<student>/<slug>/ai_context.json`) so the host AI tool and the
   container both resolve them correctly.
2. **Host (your AI assistant)** — reads each `ai_context.json`, applies the
   rubric, and writes `evaluation.json` next to it.
3. **Container** — `grade report <student>` aggregates the evaluations into
   `grades/<student>/report.md` + `report.json`, rebuilds `ui/index.html`, and
   clears the scratch.

Raw command reference (run from the repo root; `-T` disables TTY for clean output):

```powershell
docker compose run --rm -T evaluator python -m evaluator.prep survey
docker compose run --rm -T evaluator python -m evaluator.prep sync --slug task_02_calculator
docker compose run --rm -T evaluator python -m evaluator.grade plan "Gabriela Shurbeska"
docker compose run --rm -T evaluator python -m evaluator.grade report "Gabriela Shurbeska"
docker compose run --rm -T evaluator python -m evaluator.ui --no-open   # container can't open a browser
```

> **Escape hatch (no Docker):** the flow is identical with a local interpreter —
> substitute `.venv/Scripts/python.exe` (or `python`) for
> `docker compose run --rm -T evaluator python` in any command above, after the
> venv [Setup](#setup).

> **Linux note:** bind-mounted files are owned by your host user, so the image's
> non-root `uid 10001` may be denied writes to `exercises/` / `grades/`.
> Uncomment the `user:` line in `docker-compose.yml` (defaults to `${UID}:${GID}`),
> or run with `--user "$(id -u):$(id -g)"`.

## Grade dashboard (browser UI)

`/grade` rebuilds `ui/index.html` silently at the end of every run (both full
and single-task mode), so the dashboard stays in sync with `grades/`
automatically. Open the file once in your browser and refresh after each
grade run — no extra command needed.

To explicitly rebuild the page in Docker (headless — open `ui/index.html`
yourself afterward):

```powershell
docker compose run --rm -T evaluator python -m evaluator.ui --no-open
```

(Or, with the venv escape hatch, `.\.venv\Scripts\python.exe -m evaluator.ui`
also opens it in your default browser.)

This walks every `grades/<student>/report.json` and generates a single
self-contained `ui/index.html` with the data embedded inline (no HTTP server,
no `fetch` calls). Features:

- Search by student name; filter by project space.
- Sort by total points (default), pass count, name, or grading date.
- Per-student card with a colored **Total: X/Y pts** badge (green/amber/red by
  ratio) plus verdict counts (pass / fail / missing / needs prep).
- Overall summary paragraph (from `## Overall` in `report.md`).
- Collapsible per-task accordion showing each task's verdict, `points/10`
  pill, summary, failing gate (if any), and the differences list split into
  **Deductions** (with `−N pts` chip and the `rule_source`) and **Notes**
  (mention-only). Bonus-question answers are surfaced inline.

Pass `--no-open` to build the page without opening it (this is what
`/grade`'s auto-rebuild uses internally). The `ui/` folder is gitignored —
it's purely derived from `grades/`.

Exit codes:
- `0` — hard gates passed (AI step pending, or all gates passed)
- `1` — procedural hard gate failed (pipeline name mismatch)
- `2` — bad CLI args / missing required env var / unknown task slug
- `4` — deliverable not submitted (`output_present` 404 OR `triggered_task_exists` missing) — orchestrator treats as MISSING

## Adding a new exercise

1. Create `exercises/<slug>/description.md` — the student-facing prompt. The
   **first H1 heading** is the canonical pipeline name (e.g.
   `# Task 03 – Join Employee Records`); both the solution and the student's
   pipeline must use that name in SnapLogic.
2. Optionally create `exercises/<slug>/notes.md` (instructor hints — fed
   to the AI judge). Put only **task-specific** rules here; the universal
   best-practice rules in `exercises/general_evaluation_rules.md` apply
   automatically. Use `notes.md` to override a universal rule when the
   exercise legitimately requires it.
3. Run `/prep`.

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
     > `prep sync --output-csv` flag is likewise a deprecated alias for
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

See [.claude/architecture.md](.claude/architecture.md) and
[.claude/project.md](.claude/project.md) for the design rationale, plus
[.claude/conventions/](.claude/conventions/) for the running list of
project-wide and skill-scoped rules.

## Safety

The SnapLogic client is **GET-only** by construction — `SnapLogicClient`
exposes no `post`/`put`/`delete` method. If you ever need to mutate the
org (e.g., import a pipeline), it must be added explicitly and confirmed
with the project owner first.
