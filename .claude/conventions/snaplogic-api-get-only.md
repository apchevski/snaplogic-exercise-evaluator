---
name: snaplogic-api-get-only
description: All SnapLogic REST API access is GET-only. Mutating methods require explicit user approval.
scope: project-wide
---

# SnapLogic API: GET-only

All SnapLogic REST API access — production code, tests, ad-hoc probes, scripts — uses **`GET` only**. `POST` / `PUT` / `PATCH` / `DELETE` and any other mutating method is **forbidden** without explicit user approval.

**Why:** SnapLogic stores live pipelines, jobs, and project state. An accidental mutation could overwrite a student's work, delete an asset, kick off a paid pipeline run, or corrupt the tenant.

**How to apply:**
- The implementation is [evaluator/snaplogic_client.py](../../evaluator/snaplogic_client.py). It is GET-only by construction — no `.post` / `.put` / `.patch` / `.delete` method is exposed. Do not add one without first asking the user.
- When probing the API during exploration, use GETs only.
- If a legitimate feature requires a non-GET method (e.g. triggering a pipeline run), surface the requirement to the user and get explicit confirmation before implementing it.
- When in doubt: don't send the request. Ask first.

Related: [snaplogic_api_findings.md](../snaplogic_api_findings.md) (the API surface bounded by this rule).
