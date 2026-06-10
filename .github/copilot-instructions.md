# GitHub Copilot instructions — SnapLogic Exercise Evaluator

This repo grades SnapLogic training exercises. A deterministic Python engine
runs **inside Docker**; **you (Copilot, in agent mode)** are the grading judge,
running on the host. No LLM API key is used by the project itself.

**Read [`AGENTS.md`](../AGENTS.md) and follow it for any prep or grading task.**
It is the tool-neutral operating guide (prerequisites, the exact
`docker compose run …` commands, and the grade/prep flow). The detailed rubric
and the `evaluation.json` schema live in
[`.claude/skills/grade/SKILL.md`](../.claude/skills/grade/SKILL.md) and
[`.claude/skills/prep/SKILL.md`](../.claude/skills/prep/SKILL.md) — they're
plain markdown; where they name Claude Code tools (`Read`/`Write`/`Edit`), just
read/write/edit the file.

Quick reference (always run from the repo root, with Docker running and `.env`
filled in):

```
# Prep (deterministic — no judgment needed)
docker compose run --rm -T evaluator python -m evaluator.prep sync

# Grade a student
docker compose run --rm -T evaluator python -m evaluator.grade plan "<Student Name>"
#   → judge each manifest entry with status "ready_for_ai": read its
#     ai_context_path (a repo-root-relative path under .tmp/), apply the rubric,
#     write evaluation.json to its evaluation_path.
docker compose run --rm -T evaluator python -m evaluator.grade report "<Student Name>"
#   → then replace the "## Overall" placeholder in grades/<student>/report.md and run:
docker compose run --rm -T evaluator python -m evaluator.grade sync-overall "<Student Name>"
```

Then open `ui/index.html` to see the dashboard.

Hard rules:
- Never invent a points deduction — use only the explicit values written in
  `exercises/general_evaluation_rules.md` or a task's `notes.md`.
- Never modify anything under `evaluator/`, `exercises/`, or `.claude/` while
  grading; surface issues to the user instead.
- After changing `evaluator/` code, run `docker compose build` before the next
  container command.
