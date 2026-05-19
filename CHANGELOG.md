# Changelog

## [Unreleased]

- Add Python project scaffold (requirements.txt, evaluator package).
- Add GET-only SnapLogic REST client; validated against elastic.snaplogic.com.
- Add pipeline definition + SLDB CSV download (Accept */*) helpers.
- Add deterministic hard gates: pipeline name match, CSV output match.
- Add topological flow_order helper (uses link_map, not snap_map order).
- Add per-exercise task.json auto-discovery via evaluator/tasks.py.
- Pivot AI judgment from Anthropic SDK to a Claude Code skill (.claude/skills/grade); drop anthropic dependency and ANTHROPIC_API_KEY.
- Add /grade <student> slash command — iterates every registered exercise and writes an aggregated report under .tmp/grades/.
- Add SNAPLOGIC_STUDENT_PROJECT_SPACE env var (default IWC_Support) for auto-resolving student paths.
- Move /grade orchestration (project resolution, pipeline name match, report rendering) into `evaluator.grade` CLI; shrink SKILL.md from 178 to 86 lines so each /grade run consumes ~half the prompt tokens.
- Scope per-task artifacts per student under `.tmp/grades/<student>/<slug>/`; cache solution pipeline JSON in repo at `exercises/<slug>/solution.json` with a modified-at sidecar for cache invalidation. Rename `--refresh-expected-csv` → `--refresh-solution` (now also refreshes the cached pipeline JSON).
- Move agent-only docs from `context/` into `.claude/context/` (renaming `claude.md` → `CLAUDE.md` so Claude Code auto-loads it as project memory); no AI files live at repo root.
- Fix: solution cache freshness check now also requires the expected CSV on disk, so prep sync regenerates a deleted CSV instead of short-circuiting on a matching sidecar signature.
- Prep now fully reconciles `task.json` against the live solution pipeline on every survey: detects pipeline renames (heading vs `solution_pipeline_path`) and writer-filename renames (binary-write snap vs `output_csv_filename`). Adds statuses `pipeline_renamed` and `writer_changed`; sync auto-applies both in one pass.
- Prep reconcile prunes obsolete files in `exercises/<slug>/expected/`, keeping only the current `output_csv_filename`.
- Move per-student report from `.tmp/grades/<student>/report.md` to persistent `grades/<student>/report.md`; `evaluator.grade report` deletes the `.tmp/grades/<student>/` scratch dir at the end of each run. Drop the now-dangling `**Artifact**` lines from per-task report sections.
- /grade no longer asserts a missing bonus answer based on the current pipeline snapshot alone — SnapLogic's REST API does not expose per-version check-in notes from the Designer "Versions" dialog. SKILL.md now caps that finding at `minor` with a manual-check caveat; `.claude/snaplogic_api_findings.md` documents the dead-end probes so future agents don't repeat them.
- /grade now fetches the Designer "Versions" dialog comments via the discovered `/api/1/rest/pipeline/versions/{snode_id}` endpoint (verb-before-id, not `/{snode_id}/versions`) and surfaces them to the AI judge as `student_version_notes` in `ai_context.json`. SKILL.md instructs the judge to check this field first for bonus-question answers; the previous "manual-check" caveat is no longer needed.
- Fix: a missing student output CSV (404 from `/slfs/...`) no longer crashes the whole `/grade` plan loop — the evaluator converts it into a graceful `csv_output_match` hard-gate failure so the remaining tasks still run.
