---
name: report-edit-provenance
description: Mentors/admins may edit any part of a graded evaluation — including overriding points directly, which intentionally supersedes `10 − Σ deductions`. Every edit is appended to an immutable audit log; a re-grade wipes edits. Task cards show "Evaluated by AI" vs "Edited by X".
scope: project-wide
---

# Human edits win over the AI — and every edit is recorded

The project is AI-**driven**, not AI-**only**. The AI can be wrong, so a mentor
or admin must be able to correct anything it produced, and the platform must
make it clear what is machine output and what a human changed. Three rules
follow from that, enforced in `backend/src/api.py` (`patch_student_report`,
`list_report_edits`) and the React detail page.

## 1. Points can be overridden directly — this is the one place the invariant yields

The points model's core invariant is `points = 10 − Σ deductions`, "the AI never
invents a value; the same mistake costs the same points" (see
[[grade-points-system]]). A **manual points override** deliberately breaks that
formula: a mentor types a 0–10 value that pins the score regardless of the
deductions, because human judgment outranks the rubric when they disagree.

- The override is stored as `points` + `points_manual: true` on the task, and is
  allowed on **any** task — including MISSING and procedural (name-mismatch)
  FAILs, whose deductions/bonus stay locked but whose points a human may still
  set (e.g. award partial credit to a submission scored 0/—).
- Editing deductions does **not** recompute a manually-pinned task; clearing the
  override (`points: null`) falls back to the computed value.
- The **verdict/status is still never changed** — it is a hard-gate outcome, not
  a judgment call. Only points and text are editable.
- A **re-grade wipes the override** (and every other edit): new grading, new
  evaluation, fresh AI text. Edits are corrections to a specific graded state,
  not sticky policy.

Do not "fix" the override to respect `10 − Σ`; overriding it is the point. But
keep the invariant as the **default** — points track deductions unless a human
explicitly pins them.

## 2. Every edit is appended to an immutable audit log

Do not just stamp the last editor. Each applied change appends an
`AUDIT#<ts>#<rand>` row under the student's DynamoDB partition recording who,
the target (`overall` / `task:<slug>`), and each changed field as
`{field, from, to}`. Rows omit the `entity`/`slug` GSI keys so they stay out of
list queries (same trick as `REPORT#` rows). `GET /v1/students/{slug}/report/edits`
(mentor/admin only — students never see provenance) reads them back for the
**Edit history** panel. Never mutate or delete an existing audit row.

## 3. Task cards show AI-vs-human provenance

A task card shows **Evaluated by AI** when untouched (and AI-judged), or
**Edited by `<who>` · `<when>`** once a human changed it; a manually-pinned score
also shows a ✎ marker. The branching lives in `taskProvenance()` in
`frontend/src/types.ts` (keep it pure/testable). An untouched non-AI result
(MISSING / name-mismatch) shows no line — there is nothing meaningful to
attribute. Provenance is deliberately **not** shown to students or on the
dashboard (scoped that way per the feature's design).

**Related:** [[grade-points-system]] (the invariant this overrides),
[[ai-first-evaluation]] (why humans decide what matters),
[[living-documentation]] (keep README / CHANGELOG / SOLUTION_OVERVIEW current).
