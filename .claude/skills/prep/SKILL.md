---
name: prep
description: Prepare SnapLogic exercise folders for grading. Walks exercises/, creates task.json for folders missing one (by reading the canonical pipeline name from each folder's description.md H1 heading and looking up that pipeline in the solution project space), and refreshes solution.json + expected/ outputs when the SnapLogic pipeline has changed. Supports two exercise types: file-writer pipelines (one or more output files, CSV or XLSX) and triggered-task pipelines (one expected JSON response per scenario). Usage — /prep (no args, prep every folder)  OR  /prep --task <slug> (prep only one folder). Run /prep whenever you add a new exercise folder or edit a solution pipeline; /grade refuses to grade folders that are not fully prepped.
---

# /prep — Skill workflow

You (Claude) orchestrate this skill. The Python script `evaluator.sync` does all deterministic work: discovering folders, reading description.md, looking up pipelines, fetching definitions, detecting drift, and writing files. Your job is to read the survey, ask the user when the script needs disambiguation, write task.json by hand for triggered-task exercises (the only case Python can't fully derive), and re-invoke sync.

**Runtime (Docker-native).** The Python script runs in the project's Docker container — every `docker compose run …` command below must be invoked **from the repo root** (where `docker-compose.yml` lives), with Docker running and `.env` filled in. The container writes `task.json` / `solution.json` / `expected/` straight into the bind-mounted `exercises/`, so the results appear in your workspace. The hand-authored `task.json` files you write (triggered_task / multi-output) are plain host files. If you change anything under `evaluator/`, run `docker compose build` once first. (To run without Docker, substitute your local interpreter, e.g. `.venv/Scripts/python.exe -m evaluator.sync …`.)

This skill supports two modes:

- **Full prep** (default): survey and reconcile every folder under `exercises/`.
- **Single-folder prep** (`--task <slug>`): survey and reconcile only the named folder. Useful after you add one new exercise or edit one solution pipeline. The invocation is `/prep --task <slug>`; in this mode, pass `--slug <slug>` to both the `survey` and `sync` subcommands of the Python script so only that one folder is touched.

**Pipeline name convention:** the canonical pipeline name lives in the FIRST H1 heading of `exercises/<slug>/description.md` (e.g. `# Task 01 – Generate CSV Report`). Folder slugs stay snake_case; the heading is what the solution pipeline and student pipeline must both be named in SnapLogic.

**Reconciliation contract:** prep is the source-of-truth reconciler between SnapLogic and local files. Every survey re-reads the heading, looks up the pipeline live, fetches the definition, and compares against `task.json`. If anything drifted (pipeline renamed, writer filename renamed, snap structure changed, cache stale), prep detects it and — on sync — rewrites the local files to match SnapLogic. `/grade` trusts the resulting local files; it never reconciles against SnapLogic itself.

**Exercise types:** prep supports two `task_type` values in task.json:

- `file_writer` (default for back-compat) — the solution pipeline writes one or more output files via binary-write snap(s); the format is incidental (the comparison gate handles CSV and XLSX). `expected/` holds those files. Required fields: `solution_pipeline_path`, plus **either** `output_filename` (a single file) **or** `output_filenames` (an array — the pipeline writes several files and the student must reproduce **all** of them exactly). Use exactly one of the two keys. (Back-compat: the old `task_type: "csv_writer"` and `output_csv_filename` / `output_csv_filenames` keys are still accepted and normalize to these; write new task.json with the `file_writer` / `output_filename(s)` names.)
- `triggered_task` — the solution pipeline is exposed as a SnapLogic Triggered Task (asset_type=Job, metadata.type=triggered). Prep invokes the task via `GET /api/1/rest/slsched/feed/...` once per scenario and saves each response as `expected/<request_name>.json`. Required fields: `solution_pipeline_path`, `triggered_task_name`, `requests` (list of `{name, params}`).

The Python script can auto-create `task.json` for **single-output** `file_writer` exercises (the lone writer filename is in the pipeline JSON). It cannot for **multi-output** `file_writer` (it can't know whether several writers mean "one canonical output" vs "all are deliverables") or for `triggered_task` (scenarios + task name live in prose, not the pipeline JSON). In those two cases the skill (you) reads description.md + notes.md and hand-writes the file.

## Steps

### 1. Survey

Run (add `--slug "<slug>"` only when the user invoked `/prep --task <slug>`):

```
docker compose run --rm -T evaluator python -m evaluator.sync survey [--slug "<slug>"]
```

The script prints a plain summary followed by a JSON block delimited by `---SURVEY_JSON_BEGIN---` and `---SURVEY_JSON_END---`. Parse the JSON to get a list of per-folder reports. With `--slug`, the list contains exactly one report; without it, one per folder under `exercises/`.

Each report has:
- `slug`: folder name
- `status`: one of `ready`, `needs_task_json`, `needs_task_json_triggered`, `stale_solution`, `pipeline_renamed`, `writer_changed`, `ambiguous_writer`, `pipeline_not_found`, `missing_description`, `config_error`
- `task_json_exists`: bool
- `solution_pipeline_path`: the heading-derived path (the path task.json *should* hold)
- `output_filename`: file_writer only — the single writer-derived filename, or `null` when there are several outputs / it's ambiguous
- `output_filenames`: file_writer only — the full registered output list (one entry for single-output, several for multi-output)
- `proposed_writer_filenames`: every binary-write snap filename found in the live solution pipeline
- `task_type`: `file_writer`, `triggered_task`, or `null` (when task.json doesn't exist yet and the type is undecided)
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

- **`ambiguous_writer`** (file_writer only): the pipeline has multiple binary-write snaps and prep cannot auto-pick. Read `description.md` (and `notes.md` if present) to decide which case applies:

  - **One canonical output** — only one of the writers is the graded deliverable. Confirm which via `AskUserQuestion` (list `proposed_writer_filenames`), then run:

    ```
    docker compose run --rm -T evaluator python -m evaluator.sync sync --slug "<slug>" --output-file "<chosen-filename>"
    ```

  - **All writers are required deliverables** — the exercise asks the student to produce several files (e.g. "Multiple Flows"), and PASS means reproducing every one. Hand-write `exercises/<slug>/task.json` with the full list, then sync (no `--output-file`):

    ```json
    {
      "slug": "<slug>",
      "task_type": "file_writer",
      "solution_pipeline_path": "<solution_pipeline_path from the survey report>",
      "output_filenames": ["<file1>.csv", "<file2>.csv", "<file3>.csv"]
    }
    ```

    ```
    docker compose run --rm -T evaluator python -m evaluator.sync sync --slug "<slug>"
    ```

    Sync fetches every listed file into `expected/`. A multi-output task.json is hand-authored like `triggered_task` — sync refreshes its cache + expected files but never regenerates the filename list, so derive it correctly from the description.

- **`needs_task_json`** (no task.json yet, single writer detected → file_writer fast path), **`stale_solution`**, **`pipeline_renamed`**, **`writer_changed`**: all auto-fixable. Run:

  ```
  docker compose run --rm -T evaluator python -m evaluator.sync sync --slug "<slug>"
  ```

  Sync handles every drift in one pass. For single-output file_writer it rewrites `task.json` to match the heading + live writer and force-refreshes `solution.json` + sidecar + `expected/<file>`; for multi-output file_writer it preserves the hand-authored `output_filenames` list (only fixing a drifted pipeline path) and re-fetches every `expected/<file>`. For triggered_task it force-refreshes `solution.json` + sidecar and re-invokes every scenario, rewriting each `expected/<name>.json`.

- **`needs_task_json_triggered`**: no task.json AND the solution pipeline has 0 binary-write snaps — almost certainly a triggered-task exercise. The Python script cannot derive scenarios from prose; you must:

  1. Read `exercises/<slug>/description.md` (the student-facing prompt — describes the task's behavior and any input parameters).
  2. Read `exercises/<slug>/notes.md` (instructor-facing — usually specifies the expected Triggered Task name, the scenarios to exercise, and the expected response shape).
  3. Determine the canonical Triggered Task name. By the project's naming convention this is the pipeline name plus the suffix ` Task` (e.g. `Task 02 – Calculator` → `Task 02 – Calculator Task`). The triggered task name must match the SnapLogic asset exactly except for dash glyphs: hyphen-minus `-` (U+002D), en dash `–` (U+2013), and em dash `—` (U+2014) compare as equal everywhere a name is checked (see [pipeline-name-dash-tolerant](../../conventions/pipeline-name-dash-tolerant.md)). Spacing, casing, and every other character must still match. If notes.md spells it out, trust notes.md; otherwise use the convention.
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

  6. Run `sync --slug <slug>` (no `--output-file`; that flag is file_writer-only). Sync will invoke the triggered task once per scenario and write `expected/<name>.json` for each.

  Do not ask the user for scenario values — derive them from notes.md / description.md yourself. If notes.md is silent on scenarios and you genuinely cannot infer them, surface that gap to the user with a specific question (which scenarios to test) rather than guessing.

### 3. Verify

After acting on all reports, run `survey` once more (with the same `--slug` if you were in single-folder mode) and confirm every surveyed folder reads `ready`. If anything still isn't ready, list those folders and the reason in chat.

### 4. Tell the user

Print:
- One line per folder: `<slug> → <status>` (use the final survey output). In single-folder mode this is one line.
- One overall sentence (e.g., "All folders ready. Run `/grade <student>`." or "Folder X still needs your attention: <reason>.").

## Notes

- This skill writes to `exercises/<slug>/{task.json, solution.json, solution.cache.json, expected/}`. Never under `.tmp/` — that belongs to `/grade`.
- Reconcile-time cleanup: after refreshing the cache, prep deletes every file in `exercises/<slug>/expected/` that isn't currently registered — the registered output file(s) for file_writer (one or more), or any of the `<request_name>.json` files for triggered_task. Sync output prints a line per deleted file.
- Never modify anything under `evaluator/` or `.claude/`.
- The H1 heading → pipeline name rule is strict: prep does not fuzzy-match. If the heading and SnapLogic pipeline name don't agree byte-for-byte (capitalization, em-dash vs hyphen, spaces), prep returns `pipeline_not_found` and you must surface that to the user. Folder slugs are NOT used for the lookup.
- Survey does one `list_assets` + one `get_pipeline_definition` per folder so it can detect writer-filename and pipeline-rename drift. For triggered_task folders, sync additionally invokes the Triggered Task once per scenario — this counts against SnapLogic execution quota but is cached (re-runs are no-ops unless the pipeline definition changes).
- Do not ask the user before running `survey` or `sync` — the skill invocation is the authorization. Invoking a Triggered Task on the solution side is part of the prep contract; no further approval needed.
