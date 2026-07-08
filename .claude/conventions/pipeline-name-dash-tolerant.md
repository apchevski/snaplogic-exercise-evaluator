---
name: pipeline-name-dash-tolerant
description: Pipeline / Triggered-Task / pipeline-path name matching is exact EXCEPT the three dash glyphs (hyphen-minus, en dash, em dash) compare as equal. No other deviation is allowed.
scope: project-wide
---

# Pipeline name matching is dash-tolerant exact

Every name comparison in this repo — pipeline names, Triggered Task names, and pipeline-path strings (`Org/PS/Project/PipelineName`) — must be an **exact** match, with one allowed deviation: the three dash glyphs used interchangeably in SnapLogic Designer compare as equal.

| Glyph | Codepoint | Example |
|-------|-----------|---------|
| Hyphen-minus | U+002D `-` | `Task 03 - Join Employee Records` |
| En dash | U+2013 `–` | `Task 03 – Join Employee Records` |
| Em dash | U+2014 `—` | `Task 03 — Join Employee Records` |

All three count as the same name. Nothing else is tolerated: case differs → mismatch; extra/missing space → mismatch; smart quotes vs straight quotes → mismatch.

**Why:** Confirmed 2026-05-20 by the user. SnapLogic Designer renders all three glyphs almost identically, and students routinely save pipelines with a regular hyphen even when the canonical instructor name uses an en dash (or vice-versa). Failing on dash glyph alone is noise that doesn't reflect a real error; failing on case, spacing, or word choice does reflect a real error.

**How to apply:**
- Use [`evaluator/name_match.py`](../../evaluator/name_match.py) for all name comparisons:
  - `names_match(a, b)` — boolean exact-modulo-dash comparison.
  - `normalize_name(s)` — canonical form (all dashes → hyphen-minus).
  - `pipeline_paths_match(a, b)` — same rule applied to full paths.
- Do **not** lowercase, strip, accent-fold, or otherwise loosen the comparison. Only the dash glyph is fungible.
- Sites that already use the helper (keep them on it; do not regress):
  - `evaluator/hard_gates.py::check_pipeline_name_match` — the pipeline-name hard gate.
  - `evaluator/snaplogic_client.py::find_pipeline_asset_entry` — pipeline lookup by name.
  - `evaluator/snaplogic_client.py::find_triggered_task_entry` — Triggered Task lookup by name.
  - `evaluator/grade.py::_find_student_pipeline` — student-pipeline lookup during `/grade plan`.
  - `evaluator/sync.py` — `solution_pipeline_path` drift checks in `_classify_file_writer`, `_classify_triggered_task`, and `_reconcile_file_writer`.
- When you add a new name-comparison site, route it through `names_match` / `pipeline_paths_match` rather than `==`.

Related: [triggered-task-naming-strict](triggered-task-naming-strict.md) — the `<pipeline name> Task` convention enforces this rule for Triggered Task names.
