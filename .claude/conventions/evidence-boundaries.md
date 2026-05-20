---
name: evidence-boundaries
description: When reporting that something is missing or absent, name the specific search space you actually checked. Never overclaim absence.
scope: project-wide
---

# Bound negative findings to evidence

When reporting that something is missing or absent — in grading, in code review, anywhere — bound the claim to the search space you actually checked.

**Why:** A real incident: `/grade` reported "the bonus question is not answered" as a *major* issue based on `property_map.info.notes` being null. The student had written the answer in the Designer "Versions" dialog (per-checkpoint notes), a field the evaluator wasn't fetching at the time. Overclaim damages trust in the whole report — if the negatives are wrong, the user must second-guess every other finding.

**How to apply:**
- When reporting a missing thing: **name the specific fields you searched.** "Not in `version_note`, `info.notes`, `info.purpose`, sticky notes, or any snap-level notes" beats "not answered."
- If you realise mid-task that the evaluator isn't fetching a field the user expected you to see, **fix the data flow first**, then report. Soft caveats are second-best to fetching the missing field.
- Reserve "not submitted" / "missing" / "not present" language for cases with full visibility AND genuine absence.
- Symmetrical principle: when *finding* something, one positive citation is enough. When *not finding*, enumerate the search space.
