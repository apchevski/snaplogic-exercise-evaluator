# How to Operate

**1. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)
- Example: You get rate-limited on an API, so you dig into the docs, discover a batch endpoint, refactor the tool to use it, verify it works, then update the workflow so this never happens again

**2. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to. These are your instructions and need to be preserved and refined, not tossed after one use.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

This loop is how the framework improves over time.

---

# Skill conventions

## /grade

When running the `/grade` skill, do not write per-task summaries, recommendations, or overview paragraphs in chat. The user reads everything from the rendered `grades/<student>/report.md`. End the run with a single short sentence (e.g. `Grading completed.`). Do not ask for confirmation before running the `plan` or `report` subcommands — the skill invocation is the authorization.

---

# End of Instructions