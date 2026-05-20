---
name: living-documentation
description: README, CHANGELOG, and .claude/*.md have distinct non-overlapping purposes. Don't mix them.
scope: project-wide
---

# Living documentation: README / CHANGELOG / .claude

Documentation lives in three places with non-overlapping purposes. **Do not mix them.**

- **[README.md](../../README.md)** (repo root) — user-facing. What the project is, install/run, file layout, safety notes. No status indicators, no "what's next". Updated when a user-facing interface or behavior changes.
- **[CHANGELOG.md](../../CHANGELOG.md)** (repo root) — git-style. **One short sentence per change**, newest at the bottom under an `## [Unreleased]` heading (or a dated release heading). No design rationale, no walkthroughs. Updated every time a feature ships or a notable change lands.
- **`.claude/*.md`** — context for the AI agent: [CLAUDE.md](../CLAUDE.md) (operating rules), [architecture.md](../architecture.md) (design rationale + structure), [project.md](../project.md) (project framing), [snaplogic_api_findings.md](../snaplogic_api_findings.md) (REST API surface notes), and the `.claude/conventions/*.md` files. No status indicators (no ✅/⏳/⬜), no per-phase delivery notes. Updated when a design decision changes, a constraint is added, or a non-obvious lesson is worth recording.

**Why:** Each doc should be openable on its own and serve a single purpose; mixing them means readers can't trust any of them.

**How to apply:**
- A feature ships → exactly one short sentence in `CHANGELOG.md` under `[Unreleased]`. Update `README.md` only if the user-facing surface changed. Update `.claude/architecture.md` or `.claude/project.md` only if a design decision or constraint changed.
- A surprising discovery during implementation → append to `.claude/architecture.md`, or to `.claude/snaplogic_api_findings.md` if it's about the API.
- Never put status indicators in `README.md` or `.claude/*.md` — that's CHANGELOG.md's job.
- Never put narrative walkthroughs in `CHANGELOG.md` — that belongs in README.md or `.claude/architecture.md`.
