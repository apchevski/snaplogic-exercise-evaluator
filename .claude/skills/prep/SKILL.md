---
name: prep
description: Prepare SnapLogic exercise folders for grading. Walks exercises/, creates task.json for folders missing one (by reading the canonical pipeline name from each folder's description.md H1 heading and looking up that pipeline in the solution project space), and refreshes solution.json + expected/ outputs when the SnapLogic pipeline has changed. Supports two exercise types: CSV-writer pipelines (one expected CSV) and triggered-task pipelines (one expected JSON response per scenario). Usage — /prep (no args). Run /prep whenever you add a new exercise folder or edit a solution pipeline; /grade refuses to grade folders that are not fully prepped.
---

# /prep — Skill workflow

You (Claude) orchestrate this skill. The Python script `evaluator.prep` does all deterministic work: discovering folders, reading description.md, looking up pipelines, fetching definitions, detecting drift, and writing files. Your job is to read the survey, ask the user when the script needs disambiguation, write task.json by hand for triggered-task exercises (the only case Python can't fully derive), and re-invoke sync.

**Pipeline name convention:** the canonical pipeline name lives in the FIRST H1 heading of `exercises/<slug>/description.md` (e.g. `# Task 01 – Generate CSV Report`). Folder slugs stay snake_case; the heading is what the solution pipeline and student pipeline must both be named in SnapLogic.

**Reconciliation contract:** prep is the source-of-truth reconciler between SnapLogic and local files. Every survey re-reads the heading, looks up the pipeline live, fetches the definition, and compares against `task.json`. If anything drifted (pipeline renamed, writer filename renamed, snap structure changed, cache stale), prep detects it and — on sync — rewrites the local files to match SnapLogic. `/grade` trusts the resulting local files; it never reconciles against SnapLogic itself.

**Exercise types:** prep supports two `task_type` values in task.json:

- `csv_writer` (default for back-compat) — the solution pipeline writes a CSV via a binary-write snap. `expected/` holds that one CSV. Required fields: `solution_pipeline_path`, `output_csv_filename`.
- `triggered_task` — the solution pipeline is exposed as a SnapLogic Triggered Task (asset_type=Job, metadata.type=triggered). Prep invokes the task via `GET /api/1/rest/slsched/feed/...` once per scenario and saves each response as `expected/<request_name>.json`. Required fields: `solution_pipeline_path`, `triggered_task_name`, `requests` (list of `{name, params}`).

The Python script can auto-create `task.json` for `csv_writer` exercises (the writer filename is in the pipeline JSON). For `triggered_task` exercises it cannot — the scenarios and task name aren't in the pipeline JSON, they live in prose. The skill (you) reads description.md + notes.md and writes the file.

## Steps

### 1. Survey

Run:

```
.venv/Scripts/python.exe -m evaluator.prep survey
```

The script prints a plain summary followed by a JSON block delimited by `---SURVEY_JSON_BEGIN---` and `---SURVEY_JSON_END---`. Parse the JSON to get a list of per-folder reports.

Each report has:
- `slug`: folder name
- `status`: one of `ready`, `needs_task_json`, `needs_task_json_triggered`, `stale_solution`, `pipeline_renamed`, `writer_changed`, `ambiguous_writer`, `pipeline_not_found`, `missing_description`, `config_error`
- `task_json_exists`: bool
- `solution_pipeline_path`: the heading-derived path (the path task.json *should* hold)
- `output_csv_filename`: csv_writer only — the writer-derived filename, or `null` when ambiguous
- `proposed_writer_filenames`: every binary-write snap filename found in the live solution pipeline
- `task_type`: `csv_writer`, `triggered_task`, or `null` (when task.json doesn't exist yet and the type is undecided)
- `triggered_task_name`: triggered_task only — the task name from task.json
- `request_names`: triggered_task only — list of scenario names in task.json
- `expected_response_filenames`: triggered_task only — list of `<name>.json` files prep expects in expected/
- `reason`: human-readable explanation, including the specific drift detected

Exit code ≠ 0 from `survey` means a setup problem (missing `.env`, bad credentials) — surface stderr to the user and stop.

### 2. Act on each report

Apply the matching rule per entry:

- **`ready`**: nothing to do.

- **`config_error`**: surface the reason. Don't auto-repair — task.json is hand-edited or already wrong in a way the script can't fix.

- **`missing_description`**: the folder has no `description.md` or its first H1 heading is missing. Tell the user to add a heading like `# Task 01 – Generate CSV Report` as the first line of `exercises/<slug>/description.md` (it must match the SnapLogic pipeline name byte-for-byte — em-dashes, spaces, capitalization). Skip and move on.

- **`pipeline_not_found`**: the heading parsed from description.md does not exist as a pipeline in `<org>/<solution_project_space>/<solution_project>` (configured in `.env`). Surface the reason and tell the user to either create/rename the SnapLogic pipeline so it matches the heading, or fix the heading. Skip and move on.

- **`ambiguous_writer`** (csv_writer only): the pipeline has multiple binary-write snaps and prep cannot auto-pick. List the candidates in `proposed_writer_filenames` and ask via `AskUserQuestion` which is the canonical output. Then run:

  ```
  .venv/Scripts/python.exe -m evaluator.prep sync --slug "<slug>" --output-csv "<chosen-filename>"
  ```

- **`needs_task_json`** (no task.json yet, single writer detected → csv_writer fast path), **`stale_solution`**, **`pipeline_renamed`**, **`writer_changed`**: all auto-fixable. Run:

  ```
  .venv/Scripts/python.exe -m evaluator.prep sync --slug "<slug>"
  ```

  Sync handles every drift in one pass. For csv_writer it rewrites `task.json` to match the heading + live writer and force-refreshes `solution.json` + sidecar + `expected/<csv>`. For triggered_task it force-refreshes `solution.json` + sidecar and re-invokes every scenario, rewriting each `expected/<name>.json`.

- **`needs_task_json_triggered`**: no task.json AND the solution pipeline has 0 binary-write snaps — almost certainly a triggered-task exercise. The Python script cannot derive scenarios from prose; you must:

  1. Read `exercises/<slug>/description.md` (the student-facing prompt — describes the task's behavior and any input parameters).
  2. Read `exercises/<slug>/notes.md` (instructor-facing — usually specifies the expected Triggered Task name, the scenarios to exercise, and the expected response shape).
  3. Determine the canonical Triggered Task name. By the project's naming convention this is the pipeline name plus the suffix ` Task` (e.g. `Task 02 – Calculator` → `Task 02 – Calculator Task`). The triggered task name must match the SnapLogic asset byte-for-byte — same en-dash/em-dash/hyphen, same spacing. If notes.md spells it out, trust notes.md; otherwise use the convention.
  4. Derive scenarios from notes.md (and description.md if notes.md is silent). Each scenario gets a snake_case `name` (filesystem-safe — it becomes a filename in `expected/`) and a `params` dict (the query-string parameters the triggered task accepts). Cover every behavior branch notes.md flags — operator branches, error/fallback branches, edge cases.
  5. Write `exercises/<slug>/task.json` with this shape:

     ```json
     {
       "slug": "<slug>",
       "task_type": "triggered_task",
       "solution_pipeline_path": "<solution_pipeline_path from the survey report>",
       "triggered_task_name": "<derived task name>",
       "requests": [
         {"name": "<snake_case>", "params": {"<param>": "<value>"}}
       ]
     }
     ```

  6. Run `sync --slug <slug>` (no `--output-csv`; that flag is csv_writer-only). Sync will invoke the triggered task once per scenario and write `expected/<name>.json` for each.

  Do not ask the user for scenario values — derive them from notes.md / description.md yourself. If notes.md is silent on scenarios and you genuinely cannot infer them, surface that gap to the user with a specific question (which scenarios to test) rather than guessing.

### 3. Verify

After acting on all reports, run `survey` once more and confirm every folder reads `ready`. If anything still isn't ready, list those folders and the reason in chat.

### 4. Tell the user

Print:
- One line per folder: `<slug> → <status>` (use the final survey output).
- One overall sentence (e.g., "All folders ready. Run `/grade <student>`." or "Folder X still needs your attention: <reason>.").

## Notes

- This skill writes to `exercises/<slug>/{task.json, solution.json, solution.cache.json, expected/}`. Never under `.tmp/` — that belongs to `/grade`.
- Reconcile-time cleanup: after refreshing the cache, prep deletes every file in `exercises/<slug>/expected/` that isn't currently registered — the one `output_csv_filename` for csv_writer, or any of the `<request_name>.json` files for triggered_task. Sync output prints a line per deleted file.
- Never modify anything under `evaluator/` or `.claude/`.
- The H1 heading → pipeline name rule is strict: prep does not fuzzy-match. If the heading and SnapLogic pipeline name don't agree byte-for-byte (capitalization, em-dash vs hyphen, spaces), prep returns `pipeline_not_found` and you must surface that to the user. Folder slugs are NOT used for the lookup.
- Survey does one `list_assets` + one `get_pipeline_definition` per folder so it can detect writer-filename and pipeline-rename drift. For triggered_task folders, sync additionally invokes the Triggered Task once per scenario — this counts against SnapLogic execution quota but is cached (re-runs are no-ops unless the pipeline definition changes).
- Do not ask the user before running `survey` or `sync` — the skill invocation is the authorization. Invoking a Triggered Task on the solution side is part of the prep contract; no further approval needed.
