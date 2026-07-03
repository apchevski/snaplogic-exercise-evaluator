---
name: exercise-resources-folder
description: Student-facing input files live in exercises/<slug>/resources/ — never loose in the exercise root
scope: project-wide
---

# Exercise input files live in `resources/`

Any file handed to students as input data for an exercise (zips, CSVs,
Excel files, …) goes in `exercises/<slug>/resources/`, e.g.
`exercises/task_01_generate_csv_report/resources/Task1.zip`. Exercises
without input files simply have no `resources/` folder.

**Why:** before this convention (2026-07-03) input files sat loose in the
exercise root next to `description.md` / `task.json`, so nothing could
tell "student input" apart from authored/generated files without
guessing. The web UI's Exercises page lists `resources/*` as download
buttons, and `GET /v1/exercises/{slug}/resources/{filename}` serves them
via presigned S3 URLs — both discover files purely by this location.

**How to apply:**
- Adding a new exercise that ships input data → put the files in
  `exercises/<slug>/resources/`. No code changes; the UI and API pick
  them up automatically (`evaluator/tasks.py: list_exercise_resources`).
- Never write generated artifacts (solution.json, expected/, task.json)
  into `resources/` — it is exclusively student-facing input.
- The matching S3 prefix is `exercise-resources/<slug>/` (lazy-mirrored
  by the API Lambda; deliberately outside the worker-owned `exercises/`
  prefix). Don't reuse either prefix for other object types.
