---
name: prep
description: Prepare SnapLogic exercise folders for grading. Walks exercises/, creates task.json for folders missing one (by reading the canonical pipeline name from each folder's description.md H1 heading and looking up that pipeline in the solution project space), and refreshes solution.json + expected/<csv> when the SnapLogic pipeline has changed. Usage — /prep (no args). Run /prep whenever you add a new exercise folder or edit a solution pipeline; /grade refuses to grade folders that are not fully prepped.
---

# /prep — Skill workflow

You (Claude) orchestrate this skill. The Python script `evaluator.prep` does all deterministic work: discovering folders, reading description.md, looking up pipelines, fetching definitions, detecting drift, and writing files. Your job is to read the survey, ask the user when the script needs disambiguation, and re-invoke sync with the answers.

**Pipeline name convention:** the canonical pipeline name lives in the FIRST H1 heading of `exercises/<slug>/description.md` (e.g. `# Task 01 – Generate CSV Report`). Folder slugs stay snake_case; the heading is what the solution pipeline and student pipeline must both be named in SnapLogic.

**Reconciliation contract:** prep is the source-of-truth reconciler between SnapLogic and local files. Every survey re-reads the heading, looks up the pipeline live, fetches the definition, and compares against `task.json`. If anything drifted (pipeline renamed, writer filename renamed, snap structure changed, cache stale), prep detects it and — on sync — rewrites the local files to match SnapLogic. `/grade` trusts the resulting local files; it never reconciles against SnapLogic itself.

## Steps

### 1. Survey

Run:

```
.venv/Scripts/python.exe -m evaluator.prep survey
```

The script prints a plain summary followed by a JSON block delimited by `---SURVEY_JSON_BEGIN---` and `---SURVEY_JSON_END---`. Parse the JSON to get a list of per-folder reports.

Each report has:
- `slug`: folder name
- `status`: one of `ready`, `needs_task_json`, `stale_solution`, `pipeline_renamed`, `writer_changed`, `ambiguous_writer`, `pipeline_not_found`, `missing_description`, `config_error`
- `task_json_exists`: bool
- `solution_pipeline_path`: the heading-derived path (the path task.json *should* hold)
- `output_csv_filename`: the writer-derived filename (the filename task.json *should* hold), or `null` when ambiguous
- `proposed_writer_filenames`: every binary-write snap filename found in the live solution pipeline
- `reason`: human-readable explanation, including the specific drift detected

Exit code ≠ 0 from `survey` means a setup problem (missing `.env`, bad credentials) — surface stderr to the user and stop.

### 2. Act on each report

Apply the matching rule per entry:

- **`ready`**: nothing to do.
- **`config_error`**: surface the reason. Don't auto-repair — task.json is hand-edited or already wrong in a way the script can't fix.
- **`missing_description`**: the folder has no `description.md` or its first H1 heading is missing. Tell the user to add a heading like `# Task 01 – Generate CSV Report` as the first line of `exercises/<slug>/description.md` (it must match the SnapLogic pipeline name byte-for-byte — em-dashes, spaces, capitalization). Skip and move on.
- **`pipeline_not_found`**: the heading parsed from description.md does not exist as a pipeline in `<org>/<solution_project_space>/<solution_project>` (configured in `.env`). Surface the reason and tell the user to either create/rename the SnapLogic pipeline so it matches the heading, or fix the heading. Skip and move on.
- **`ambiguous_writer`**: the pipeline has zero or multiple binary-write snaps and prep cannot auto-pick. The `reason` field explains whether this is initial prep (no task.json yet) or a drift (task.json's filename is no longer in the writer set). List the candidates in `proposed_writer_filenames` and ask via `AskUserQuestion` which is the canonical output. Then run:

  ```
  .venv/Scripts/python.exe -m evaluator.prep sync --slug "<slug>" --output-csv "<chosen-filename>"
  ```

- **`needs_task_json`** (no task.json yet, single writer detected), **`stale_solution`** (cache sig stale or files missing), **`pipeline_renamed`** (heading-derived path differs from task.json), **`writer_changed`** (single-writer filename differs from task.json): all auto-fixable. Run:

  ```
  .venv/Scripts/python.exe -m evaluator.prep sync --slug "<slug>"
  ```

  A single sync handles every drift in one pass — it rewrites `task.json` to match the heading + live writer, then force-refreshes `solution.json` + sidecar + `expected/<csv>` from SnapLogic.

### 3. Verify

After acting on all reports, run `survey` once more and confirm every folder reads `ready`. If anything still isn't ready, list those folders and the reason in chat.

### 4. Tell the user

Print:
- One line per folder: `<slug> → <status>` (use the final survey output).
- One overall sentence (e.g., "All folders ready. Run `/grade <student>`." or "Folder X still needs your attention: <reason>.").

## Notes

- This skill writes to `exercises/<slug>/{task.json, solution.json, solution.cache.json, expected/}`. Never under `.tmp/` — that belongs to `/grade`.
- Reconcile-time cleanup: after refreshing the cache, prep deletes every file in `exercises/<slug>/expected/` that isn't the current `output_csv_filename`. This keeps stale CSVs from accumulating when a writer is renamed in SnapLogic. Sync output prints a line per deleted file.
- Never modify anything under `evaluator/` or `.claude/`.
- The H1 heading → pipeline name rule is strict: prep does not fuzzy-match. If the heading and SnapLogic pipeline name don't agree byte-for-byte (capitalization, em-dash vs hyphen, spaces), prep returns `pipeline_not_found` and you must surface that to the user. Folder slugs are NOT used for the lookup.
- Survey is more expensive than it used to be: it does one `list_assets` + one `get_pipeline_definition` per folder so it can detect writer-filename and pipeline-rename drift. This is intentional — prep runs occasionally, not on every grade, and the cost is what guarantees /grade can trust local files.
- Do not ask the user before running `survey` or `sync` — the skill invocation is the authorization.
