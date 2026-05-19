# SnapLogic Exercise Evaluator

Automated grading for SnapLogic training exercises. AI-driven judgment via a
Claude Code skill — no Anthropic API key, no per-evaluation cost. Designed for
exercises that admit many correct solutions, so judgment comes from a model
rather than a rubric.

## What it does

You invoke a single slash command in Claude Code:

```
/grade Gabriela Shurbeska
```

The `grade` skill then:

1. Resolves the student's project location from `.env` defaults (org +
   `SNAPLOGIC_STUDENT_PROJECT_SPACE` + student name → project path).
2. Discovers every registered exercise from `exercises/*/task.json`.
3. For each exercise, runs the deterministic Python evaluator which:
   - Fetches both the solution pipeline and the student's pipeline (GET-only).
   - Applies hard gates: pipeline name match, output CSV match.
   - On hard-gate fail → writes a complete `evaluation.json` and stops.
   - On hard-gate pass → writes an `ai_context.json` bundle (description,
     instructor notes, topologically-sorted snap flows, both raw pipeline
     JSONs) and emits `READY_FOR_AI_REVIEW`.
4. The skill picks up from there: reads the context bundle, judges
   structural differences in-conversation, and writes the final
   `evaluation.json`. **The AI step runs inside your Claude Code session
   — no API calls.**
5. Composes `grades/<student>/report.md` aggregating every exercise. Scratch
   artifacts under `.tmp/grades/<student>/` are deleted at the end of the run —
   only `report.md` persists.

## Project layout

```
.
├── README.md
├── CHANGELOG.md
├── LICENSE
├── requirements.txt
├── .env.example                # template; copy to .env and fill in
├── .claude/
│   ├── context/                # CLAUDE.md (operating rules) + architecture/project notes
│   └── skills/grade/SKILL.md   # the /grade slash command
├── exercises/
│   ├── general_evaluation_rules.md
│   └── task_01_generate_csv_report/
│       ├── task.json           # solution_pipeline_path + output_csv_filename
│       ├── description.md      # the student-facing prompt
│       ├── notes.md            # instructor hints fed to the AI judge
│       ├── Task1.zip           # student-facing input data
│       ├── solution.json       # cached solution pipeline JSON (committed)
│       ├── solution.cache.json # sidecar: signature + snode_id for cache invalidation
│       └── expected/           # golden output CSV (auto-fetched alongside solution.json; only the current writer's filename is kept)
├── grades/                     # persistent per-student report.md files (written by `/grade`)
├── evaluator/
│   ├── __init__.py
│   ├── __main__.py             # `python -m evaluator ...`
│   ├── config.py               # env loading
│   ├── snaplogic_client.py     # GET-only SnapLogic REST client
│   ├── pipeline_fetch.py       # pipeline + SLDB file retrieval, topo sort
│   ├── hard_gates.py           # name + output equality checks
│   ├── tasks.py                # task.json discovery + TaskConfig
│   └── evaluate.py             # orchestrator + CLI (no LLM call)
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

`SNAPLOGIC_STUDENT_PROJECT_SPACE` defaults to `IWC_Support` if not set.

## Running

**Primary entry point — the slash command in Claude Code:**

```
/grade Gabriela Shurbeska
```

or with an explicit project space:

```
/grade --space Test_Antonio SnapLogic_Training_Program
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
students hits the cache.

Flags:
- `--student-name <name>` — override the auto-derived student name
  (used in the output path).
- `--refresh-solution` — force a refetch of both `solution.json` and the
  expected CSV, ignoring the cached signature.

Exit codes:
- `0` — hard gates passed (AI step pending, or all gates passed)
- `1` — hard gate failed
- `2` — bad CLI args / missing required env var / unknown task slug

## Adding a new exercise

1. Create `exercises/<slug>/description.md` (student-facing prompt).
2. Optionally create `exercises/<slug>/notes.md` (instructor hints — fed
   to the AI judge).
3. Create `exercises/<slug>/task.json`:

   ```json
   {
     "solution_pipeline_path": "Org/ProjectSpace/Project/Pipeline Name",
     "output_csv_filename": "result.csv"
   }
   ```

No Python edits needed. The skill auto-discovers any folder with a
`task.json`.

## Architecture & design notes

See [.claude/context/architecture.md](.claude/context/architecture.md) and
[.claude/context/project.md](.claude/context/project.md) for the design rationale.

## Safety

The SnapLogic client is **GET-only** by construction — `SnapLogicClient`
exposes no `post`/`put`/`delete` method. If you ever need to mutate the
org (e.g., import a pipeline), it must be added explicitly and confirmed
with the project owner first.
