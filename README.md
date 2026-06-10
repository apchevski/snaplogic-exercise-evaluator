# SnapLogic Exercise Evaluator

Automated grading for SnapLogic training exercises. AI-driven judgment via a
Claude Code skill — no Anthropic API key, no per-evaluation cost. Designed for
exercises that admit many correct solutions, so judgment comes from a model
rather than a rubric.

## What it does

Two slash commands in Claude Code:

```
/prep                          # reconcile every exercise folder against SnapLogic
/grade Gabriela Shurbeska      # grade one student against the registered exercises
```

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
├── .env.example                # template; copy to .env and fill in
├── .claude/
│   ├── CLAUDE.md               # operating rules (auto-loaded by Claude Code)
│   ├── architecture.md         # design notes
│   ├── project.md              # project framing
│   ├── snaplogic_api_findings.md  # REST API discoveries / gotchas
│   ├── settings.json           # Claude Code project settings
│   ├── conventions/            # one file per project-wide or skill-scoped rule
│   └── skills/
│       ├── grade/SKILL.md      # the /grade slash command
│       └── prep/SKILL.md       # the /prep slash command
├── exercises/
│   ├── general_evaluation_rules.md
│   ├── task_01_generate_csv_report/   # file_writer example
│   │   ├── task.json           # solution_pipeline_path + output_filename (or output_filenames[] for multi-output)
│   │   ├── description.md      # the student-facing prompt (H1 = canonical pipeline name)
│   │   ├── notes.md            # instructor hints fed to the AI judge
│   │   ├── Task1.zip           # student-facing input data
│   │   ├── solution.json       # cached solution pipeline JSON (committed)
│   │   ├── solution.cache.json # sidecar: signature + snode_id for cache invalidation
│   │   └── expected/           # golden output file(s) (auto-fetched; only registered writer filenames are kept)
│   └── task_02_calculator/     # triggered_task example
│       ├── task.json           # solution_pipeline_path + triggered_task_name + requests[]
│       ├── description.md
│       ├── notes.md
│       ├── solution.json
│       ├── solution.cache.json
│       └── expected/           # one <scenario>.json per request in task.json
├── grades/                     # persistent per-student report.md + report.json (written by `/grade`)
├── evaluator/
│   ├── __init__.py
│   ├── __main__.py             # `python -m evaluator ...`
│   ├── config.py               # env loading
│   ├── snaplogic_client.py     # GET-only SnapLogic REST client
│   ├── pipeline_fetch.py       # pipeline + SLDB file retrieval, topo sort, triggered-task probes
│   ├── name_match.py           # dash-tolerant pipeline-name comparison
│   ├── hard_gates.py           # name + output equality checks (CSV/XLSX or per-scenario JSON)
│   ├── tasks.py                # task.json discovery + TaskConfig (file_writer | triggered_task)
│   ├── evaluate.py             # per-task evaluator (no LLM call)
│   ├── prep.py                 # /prep skill orchestrator + CLI
│   └── grade.py                # /grade skill orchestrator + CLI
└── .tmp/                       # scratch space during a grading run; cleaned out per student at the end of `/grade report`
```

## Setup

```powershell
# from repo root
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# fill in credentials
Copy-Item .env.example .env
notepad .env   # set SNAPLOGIC_* values
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
`python -m evaluator.grade {plan,report,sync-overall}`. The skills under
`.claude/skills/` document the exact invocations. `sync-overall` is a small
helper that copies the rendered `## Overall` paragraph from `report.md` into
`overall_summary` inside `report.json`; the `/grade` skill calls it after
filling in the Overall paragraph (full mode only).

## Grade dashboard (browser UI)

`/grade` rebuilds `ui/index.html` silently at the end of every run (both full
and single-task mode), so the dashboard stays in sync with `grades/`
automatically. Open the file once in your browser and refresh after each
grade run — no extra command needed.

To explicitly build the page (and open it in the default browser):

```powershell
.\.venv\Scripts\python.exe -m evaluator.ui
```

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
