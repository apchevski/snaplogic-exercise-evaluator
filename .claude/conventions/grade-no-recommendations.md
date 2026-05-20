---
name: grade-no-recommendations
description: Grade reports must not include a `## Recommendations` section. Only Overall + per-task sections.
scope: skill:grade
---

# /grade — no `## Recommendations` section in the report

The rendered `grades/<student>/report.md` must NOT contain a `## Recommendations` section. Only `## Overall` and the per-task sections.

**Why:** The user finds recommendations bullets redundant — the per-task `summary` and failing-gate details already tell the student what to fix. Extra bullets restate the same content and pad the report.

**How to apply:**
- The footer block was removed from [evaluator/grade.py](../../evaluator/grade.py) (the `_render_task_section` / report-rendering path). Do not re-add it if you regenerate the template.
- The skill instructions in [`.claude/skills/grade/SKILL.md`](../skills/grade/SKILL.md) reflect this — do not reintroduce a "Recommendations" TODO when editing the skill.
- Report structure: header → `## Overall` → per-task sections → end.

Related: [living-documentation](living-documentation.md) — the report is the user-facing artifact; don't pad it.
