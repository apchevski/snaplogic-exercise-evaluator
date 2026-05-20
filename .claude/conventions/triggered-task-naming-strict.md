---
name: triggered-task-naming-strict
description: For triggered_task exercises, the Triggered Task name must be exactly `<pipeline name> Task` (dash glyphs are interchangeable, nothing else). A correctly-behaving task under any other name is a hard-gate fail.
scope: project-wide
---

# Triggered Task naming convention is strict

For every `triggered_task` exercise, the Triggered Task in the student's project **must** be named `<pipeline name> Task` — pipeline name plus the literal suffix ` Task`. For example, the pipeline `Task 02 – Calculator` requires a Triggered Task named `Task 02 – Calculator Task` — same spacing, same casing, same words.

Dash glyphs are the only allowed deviation: hyphen-minus `-` (U+002D), en dash `–` (U+2013), and em dash `—` (U+2014) compare as equal everywhere a name is checked, so `Task 02 - Calculator Task` (hyphen) also passes against `Task 02 – Calculator Task` (en dash). See [pipeline-name-dash-tolerant](pipeline-name-dash-tolerant.md) for the full rule. Nothing else is tolerated.

This is a **hard gate**, not a soft preference. The Python evaluator looks up the student's task by this dash-tolerant exact name; if no task with that name exists, the gate fails and the AI evaluator is not invoked — even when a correctly-behaving Triggered Task exists in the project under a different name.

**Why:** Confirmed 2026-05-20 by the user when designing `/grade` for triggered-task exercises. The student-facing instructions teach this convention explicitly, so we enforce it by gate rather than by AI judgment. Dash tolerance was added 2026-05-20 because the three dash glyphs are visually indistinguishable in SnapLogic Designer and students routinely paste hyphens where the canonical name has an en dash.

**How to apply:**
- In `evaluator/evaluate.py`, the `triggered_task_exists` gate uses `client.find_triggered_task_entry(...)`, which compares names via `evaluator.name_match.names_match` (dash-tolerant exact). Do not add a "single task in project" fallback or any broader fuzzy match.
- In `exercises/<slug>/notes.md` for triggered-task exercises, document this as a fail (not a minor issue).
- When adding a new triggered-task exercise via `/prep`, derive the task name as `<pipeline name> Task` (the convention also lives in [.claude/skills/prep/SKILL.md](../skills/prep/SKILL.md)).

Related: [snaplogic-api-get-only](snaplogic-api-get-only.md) — invoking the student's task is a GET against the cloud URL with basic auth; no mutation. [pipeline-name-dash-tolerant](pipeline-name-dash-tolerant.md) — the dash-glyph rule that applies to every name check in the codebase.
