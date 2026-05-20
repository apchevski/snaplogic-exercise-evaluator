---
name: ai-first-evaluation
description: Exercise evaluation is AI-driven; deterministic code only handles unambiguous failures. Rubric-based grading was explicitly rejected.
scope: project-wide
---

# AI-first evaluation, not rubric-first

Exercise evaluation is **AI-driven**. Hardcoded rules and rubric files are not the design. Direct quote from the user: *"You will be the one to determine if the differences in the pipelines are not important or they are, not a hardcoded file."*

**Why:** Exercises become more complex over time and admit many correct solutions. A rubric inevitably mis-flags legitimate alternatives as failures. The project stays simple by letting the AI handle judgment.

**How to apply:**
- Reserve deterministic checks ("hard gates") for **unambiguous** fail conditions only — currently: pipeline name exact-match and output file exact-match. These short-circuit obvious failures cheaply; they do not grade.
- Everything that requires judgment (snap order, snap config differences, parameter choices, bad-practice detection) goes into the AI evaluator's prompt as *hints*, not rules. Use `exercises/<slug>/notes.md` to feed those hints.
- When tempted to add a new deterministic comparator, ask: "Is this truly unambiguous, or am I encoding a judgment call?" If it's a judgment call, move it into the AI prompt instead.
- A prior rubric schema (`snap_exists`, `connection_exists`, `config_match`, `count_check`) was rejected and removed — do not reintroduce it.

Related: [canonical pipeline form](../architecture.md) (raw SnapLogic JSON, no IR layer).
