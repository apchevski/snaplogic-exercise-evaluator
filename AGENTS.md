# AGENTS.md — driving this project with any AI assistant

This file tells **any agentic AI coding assistant** (Claude Code, GitHub Copilot
agent mode, Cursor, …) how to prep exercises and grade students in this repo.
It is the tool-neutral version of the two Claude Code skills under
[`.claude/skills/`](.claude/skills/) — if you are Claude Code, the `/prep` and
`/grade` slash commands already encode all of this; everyone else follows the
steps here.

## The model (read this first)

This project has two halves:

1. **A deterministic engine** — the Python package `evaluator/`, which runs
   **inside Docker**. It talks to the SnapLogic REST API (GET-only), runs the
   hard gates, and renders reports. You never need Python installed locally.
2. **The judgment** — for grading, *you (the AI assistant)* are the judge. The
   engine hands you a context bundle; you apply the rubric and write a verdict
   file. **The AI step is you, running on the host — it is not in the
   container.** No Anthropic/OpenAI API key is used by this project.

The two halves meet through **bind-mounted folders**. `docker-compose.yml` maps
the repo into the container, so files the engine writes (`exercises/…`,
`.tmp/…`, `grades/…`, `ui/index.html`) appear directly in your workspace, and
files you write (`evaluation.json`, hand-authored `task.json`) are seen by the
container. You orchestrate by (a) running `docker compose run …` commands and
(b) reading/writing plain files in the workspace.

> **Tooling note for non-Claude agents:** the detailed procedures live in
> [`.claude/skills/grade/SKILL.md`](.claude/skills/grade/SKILL.md) and
> [`.claude/skills/prep/SKILL.md`](.claude/skills/prep/SKILL.md). They're plain
> markdown — follow them. Where they name Claude Code tools (`Read`, `Write`,
> `Edit`, `AskUserQuestion`), just do the obvious equivalent: read a file, write
> a file, edit a file, ask the user.

## Prerequisites (one-time, per machine)

1. **Docker** running (Desktop on Windows/macOS, Engine on Linux).
2. **`.env`** at the repo root: `Copy-Item .env.example .env` (or `cp`), then
   fill in the `SNAPLOGIC_*` values. Needs SnapLogic **admin** credentials with
   read access to the solution space and the student spaces. See the README
   "Setup" table for each variable.
3. The solution pipelines must exist in your SnapLogic org under the names in
   each `exercises/<slug>/description.md` H1 heading (this is what prep fetches).
4. **Build the image once:** `docker compose build`. Re-run it only if anything
   under `evaluator/` changes.

Always run the commands below **from the repo root** (where `docker-compose.yml`
is). `--rm -T` cleans up the one-shot container and disables TTY allocation so
output is captured cleanly.

## Prep — reconcile exercises with SnapLogic (fully deterministic, no AI judgment)

Prep needs **no AI judgment** — it's pure engine work. Run:

```
docker compose run --rm -T evaluator python -m evaluator.prep survey
docker compose run --rm -T evaluator python -m evaluator.prep sync          # all folders
docker compose run --rm -T evaluator python -m evaluator.prep sync --slug <slug>   # one folder
```

`survey` prints a per-folder status (and a JSON block between
`---SURVEY_JSON_BEGIN---` / `---SURVEY_JSON_END---`). `sync` writes
`solution.json` + `expected/` and, for single-output file_writer exercises,
`task.json`. The committed `task.json` files already carry the hand-authored
intent for triggered-task / multi-output exercises, and `sync` rewrites their
`solution_pipeline_path` to match **your** org/space automatically — so for a
fresh clone, `prep sync` is usually all you need.

Only when `survey` reports `needs_task_json_triggered` or `ambiguous_writer`
for a brand-new exercise do you hand-author `task.json` first — follow
[`.claude/skills/prep/SKILL.md`](.claude/skills/prep/SKILL.md) step 2 for the
exact schema, then `sync`.

## Grade — score one student (you are the judge)

```
# 1. Run the hard gates for every exercise (one container, loops all tasks).
docker compose run --rm -T evaluator python -m evaluator.grade plan "<Student Name>"
#    Optional: [--space "<project_space>"]  [--task "<slug>"]
```

This writes `.tmp/grades/<student>/manifest.json`. Each entry has a `status`:

- `ready_for_ai` → **you judge it** (next step).
- `fail` / `missing` / `needs_prep` / `config_error` → already decided; nothing
  to judge. `needs_prep` means run `prep` first.

```
# 2. Judge every entry whose status is "ready_for_ai".
```
For each such entry, read its `ai_context_path` and write your verdict to its
`evaluation_path`. **Both are repo-root-relative paths** (e.g.
`.tmp/grades/<student>/<slug>/ai_context.json`) — they sit in your workspace via
the bind mount. The bundle contains the description, the rubric
(`general_rules` + `task_notes`), both pipelines' topologically-sorted flows,
and the hard-gate results. Apply the rubric and emit the `evaluation.json`
schema **exactly** as specified in
[`.claude/skills/grade/SKILL.md`](.claude/skills/grade/SKILL.md) §2 (verdict,
points = `max(0, 10 − Σ deductions)`, `differences[]` with explicit
`points_deducted` + `rule_source`, `bonus_question_answer`). Never invent a
deduction value — use only the explicit values in the rule text.

```
# 3. Render the aggregated report (reads your evaluation.json files).
docker compose run --rm -T evaluator python -m evaluator.grade report "<Student Name>"
#    Pass the same --task / --space you passed to plan, if any.
```

`report` writes `grades/<student>/report.md` + `report.json`, rebuilds
`ui/index.html`, and clears `.tmp/`. In full mode it leaves one `## Overall`
placeholder in `report.md` — replace it with a short (1–2 sentence), general
synthesis (no per-task callouts, no recommendations; see the SKILL for the
exact rule), then:

```
docker compose run --rm -T evaluator python -m evaluator.grade sync-overall "<Student Name>"
```

## See the result

Open `ui/index.html` in your browser (it's a self-contained dashboard of every
`grades/<student>/report.json`). Or read `grades/<student>/report.md` directly.

## Grade all students

There is no single bulk command yet — enumerate the student names (the project
folders under your `SNAPLOGIC_STUDENT_PROJECT_SPACE`) and repeat the Grade
section per student. The dashboard aggregates everyone automatically.

## Gotchas

- **Docker must be running**, and you must be **in the repo root**, for any
  command to work.
- **Code changes need a rebuild:** after editing `evaluator/`, run
  `docker compose build` or the container keeps the old code.
- **Triggered-task prep/grade executes pipelines** server-side (SnapLogic
  execution quota); results are cached, so re-runs are no-ops unless the
  pipeline changed.
- The container can't open a browser — the UI is rebuilt as a file; open it
  yourself from the host.
