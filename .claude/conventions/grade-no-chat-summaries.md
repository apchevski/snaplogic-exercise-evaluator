---
name: grade-no-chat-summaries
description: When running /grade, do not write per-task summaries or overview paragraphs as chat text. End the run with one short sentence.
scope: skill:grade
---

# /grade — no per-task summaries in chat

Do not write per-task summaries, recommendations, or overview paragraphs as chat text when running `/grade`. The user reads everything from the rendered `grades/<student>/report.md`. End the run with a single short sentence (e.g. `Grading completed.`).

**Why:** Duplicating the report in chat wastes the user's attention — they already have the persistent artifact. The chat output should be the bare minimum needed to confirm the run completed and point at the file.

**How to apply:**
- After the `report` subcommand finishes, print only the per-task verdict lines that subcommand already emits, plus the report path, plus one short closing sentence.
- Don't add a "Here's what I found across the tasks..." paragraph. Don't restate findings. Don't preview the Overall section.
