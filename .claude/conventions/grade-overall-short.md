---
name: grade-overall-short
description: The /grade report's `## Overall` must be short (1–2 sentences), general (no specific tasks), and purely descriptive (no recommendations or suggestions for improvement).
scope: skill:grade
---

# /grade — the `## Overall` paragraph is short, general, and descriptive-only

The `## Overall` section of `grades/<student>/report.md` has three hard limits:

1. **Short.** 1–2 sentences, no more. Lead with the headline (pass/fail count + total points), then characterize overall performance / recurring themes in general terms. Stop there.
2. **General — no specific tasks.** Never mention or enumerate individual tasks/slugs or their issues (no "Task 02's triggered task 404s", "Task 03 leaves an extra column", etc.). Recurring best-practice *categories* stated generally (e.g. "points were lost to snaps left at default names") are fine as description; per-exercise callouts are not. The per-task sections already carry all the detail.
3. **Descriptive only — no recommendations.** No suggestions for improvement, no next steps, no "the single highest-value area to improve", no "the area to focus on", no "fixing X would make it flawless"-style advice. The Overall only *describes* how the submission did; it never advises the student what to do next.

**Why:** The user has refined the Overall twice. First (2026-06-06): "only write general things and not mention specific tasks." Then (2026-06-07): "I want the Overall SHORT. I don't want TASK SPECIFIC comments in the overall. I don't want recommendations for future or suggestions for improvements." A long, advisory, task-by-task Overall duplicates the per-task sections and the (already-removed) Recommendations footer; the user wants the top of the report to be a quick, neutral characterization, nothing more.

**How to apply:**
- Canonical operational instruction lives in [`.claude/skills/grade/SKILL.md`](../skills/grade/SKILL.md) step 3 — both the full-mode Overall instruction and the single-task-mode "Refresh `## Overall`" step enforce all three limits. Follow SKILL.md; this file is the durable why + how.
- The `## Overall` TODO placeholder in [`evaluator/grade.py`](../../evaluator/grade.py) (`cmd_report`) states the same constraints — keep it in sync if you touch either.
- The distinction that trips people up: describing a recurring weakness as an *observation* ("the only point losses came from a recurring Pass-through habit") is allowed; framing it as *advice* ("closing that gap would make the set flawless") is not.

Related: [grade-no-recommendations](grade-no-recommendations.md) (no `## Recommendations` *section* anywhere in the report — this convention extends that "no advice" principle into the Overall paragraph) and [grade-no-chat-summaries](grade-no-chat-summaries.md).
