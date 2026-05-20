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

## Where to record new findings and rules

When something new is worth remembering across sessions, default to writing it into `.claude/` rather than auto-memory. Auto-memory loads conditionally; `.claude/*.md` and `CLAUDE.md` load every session and are visible to teammates.

- **Project-wide rule or convention** ("from now on, do X"; "never do Y") → create `.claude/conventions/<slug>.md` (frontmatter `name`, `description`, `scope: project-wide`).
- **Skill-specific behavior rule** ("/grade should not X") → create `.claude/conventions/<skill>-<slug>.md` (frontmatter `scope: skill:<name>`).
- **Architectural decision or design rationale** → add to [.claude/architecture.md](architecture.md).
- **Project framing / philosophy / environment** → add to [.claude/project.md](project.md).
- **SnapLogic REST API discovery** (endpoint works / doesn't work, response shape, gotcha) → add to [.claude/snaplogic_api_findings.md](snaplogic_api_findings.md).
- **A new category of finding** that doesn't fit any existing file → create a new `.claude/<topic>.md`.

Reserve auto-memory for: the user's personal preferences and role, ephemeral project state (deadlines, in-flight work), and breadcrumbs that preserve the user's quotes / incident context behind a `.claude/` rule.

When in doubt: write it to `.claude/`. Duplication into memory is unnecessary — auto-memory entries should be short pointers to the canonical `.claude/` location, not copies.

---

# Keep README.md and CHANGELOG.md current

Whenever you add a feature, change behavior, rename a flag/env var, restructure folders, or otherwise modify how the project is used, **update BOTH `README.md` AND `CHANGELOG.md` in the same change**. Don't defer it — the docs drift fast.

- **README.md** is the user-facing manual. Touch it whenever the user-visible surface changes: new slash command, new flag, renamed env var, new exercise type, changed project layout, new setup step.
- **CHANGELOG.md** is the running log of changes (under `## [Unreleased]`). Add one bullet per logically distinct change. Lead with the "what changed" and add the "why" only when it's not obvious from the bullet.

If a change is purely internal (refactor with no behavior change, new convention file, comment-only edits), skip the README but still add a brief CHANGELOG bullet so reviewers see it.

If you're unsure whether a change is user-visible, default to updating both.

---

# Conventions

All project-wide and skill-scoped rules live in their own files under [.claude/conventions/](conventions/). Read the relevant file when a rule applies. Skill workflows themselves live in the SKILL.md under [.claude/skills/](skills/); background architecture / project framing lives in [.claude/architecture.md](architecture.md) and [.claude/project.md](project.md).

When you add a new convention, create `.claude/conventions/<slug>.md` with frontmatter `name`, `description`, and `scope` (`project-wide`, or `skill:<name>` for skill-scoped rules — prefix the slug with the skill name in that case, e.g. `grade-no-recommendations.md`).

---

# End of Instructions
