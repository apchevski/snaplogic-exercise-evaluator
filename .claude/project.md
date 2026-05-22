# Project Context

## What this is

An AI-driven evaluator for SnapLogic training exercises. The user
(Antonio) reviews student submissions for the SnapLogic Training
Program at InterWorks. This project automates the comparison between
each student's pipeline and the official solution.

## Core philosophy

**AI-first, not rubric-first.** Exercises become increasingly complex
and admit many correct solutions. A static rubric cannot capture
"correctness" — only an AI can read two pipelines and judge whether
structural differences matter. The system supports the AI with:

- Deterministic hard gates (cheap, unambiguous failures caught before
  AI is invoked).
- Instructor-written `notes.md` per exercise (hints for the AI, not
  strict rules).
- A general rules document (`exercises/general_evaluation_rules.md`).

The AI receives raw SnapLogic pipeline JSON as the canonical form.
There is intentionally no internal "Pipeline IR" abstraction.

## SnapLogic environment

- Org: `Interworks-Partner`
- Solution project: `Test_Antonio/SnapLogic_Training_Program`
- Student pipelines live in other project spaces, e.g.
  `IWC_Support/<Student Name>`.
- API access: SnapLogic Public API, basic auth via admin credentials in
  `.env`. **GET-only.** No mutating calls.

## First exercise

`task_01_generate_csv_report` — students unzip a provided ZIP, parse a
CSV, filter California residents, sort by last name descending, and
write `CA_Birthdays.csv`. Critical instructor concern: students often
place the Sort snap *before* the Filter snap, which is wasteful.
That's the universal `filter before sort` rule in
`exercises/general_evaluation_rules.md`, worth `-2 points` whenever it
applies (see [.claude/conventions/grade-points-system.md](conventions/grade-points-system.md)).

## Living docs rule

`README.md`, `CHANGELOG.md`, and `.claude/context/*.md` must stay in sync with
the code on every meaningful change. (Auto-memory:
`feedback_living_docs`.)
