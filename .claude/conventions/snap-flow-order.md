---
name: snap-flow-order
description: Pipeline execution order comes from link_map (topo-sorted), not snap_map iteration. Never infer flow order from snap_map.
scope: project-wide
---

# Snap flow order comes from `link_map`, not `snap_map`

Pipeline execution order lives in `link_map`. **Never** claim a snap is "before" or "after" another by reading `snap_map` alone.

**Why:** `snap_map` is a UUID-keyed dict; iteration order is insertion order, which only coincidentally matches flow for trivially linear pipelines and breaks after any edit/reorder in the Designer. A real incident (2026-05-18): an AI claim about a student's File Writer / CSV Formatter ordering was fabricated by iterating `snap_map.items()` and assuming that was flow order. It wasn't.

**How to apply:**
- Always derive flow order from `link_map`. Use `flow_order(pipeline_definition)` from [evaluator/pipeline_fetch.py](../../evaluator/pipeline_fetch.py) (Kahn topological sort).
- When passing snap-order info to the AI evaluator, pass the topologically-sorted list explicitly so the model isn't tempted to infer from raw JSON either.
- More generally: **don't fabricate.** If a claim about external state can be cheaply verified, verify it first. A wrong assertion stated confidently is worse than admitting uncertainty.
