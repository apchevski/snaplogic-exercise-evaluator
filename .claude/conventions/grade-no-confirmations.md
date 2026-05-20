---
name: grade-no-confirmations
description: Do not ask the user to confirm before running /grade's `plan` or `report` subcommands. The skill invocation is the authorization.
scope: skill:grade
---

# /grade — no confirmation prompts

Do not ask the user to confirm before running the `plan` or `report` subcommands of `/grade`. The skill invocation (`/grade <student>`) is the authorization.

**Why:** The user has already opted into the workflow by invoking the slash command. Asking "shall I run plan now?" adds friction without adding safety — the subcommands are deterministic and idempotent (plan writes a manifest, report renders from per-task files and clears `.tmp/`).

**How to apply:**
- After parsing `<student>` and any flags, run `plan` immediately.
- After judging all `ready_for_ai` tasks, run `report` immediately.
- Only stop and ask if `plan` itself reports a `config_error` or other setup problem that the user must resolve.
