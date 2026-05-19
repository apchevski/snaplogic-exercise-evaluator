# SnapLogic REST API — what we can and can't see

Notes for future agents working on this evaluator. Keep this in sync
with `evaluator/snaplogic_client.py` when you discover something new.

## What works (used today)

- `GET /api/1/rest/asset/list/{org}/{ps}/{project}` — lists assets.
  Each entry has: `name, snode_id, asset_type, path, time_created,
  time_updated, owner, updated_by, parent_snode_id, partition_snode_id,
  org_snode_id, acl, perms, metadata, lease_info, time_leased,
  target_runtime, asset_id`. Asset types observed in our org:
  `Account, File, Job, Pipeline, Plex` — no `Version` / `PipelineVersion`.
- `GET /api/1/rest/asset/{org}/{ps}/{project}/{name}` — single-asset
  metadata. Same keys as the list entry plus `org_snode_id`. `metadata`
  is `{"tags": []}` for pipelines.
- `GET /api/1/rest/pipeline/{snode_id}` — full pipeline definition
  (latest snapshot): `snap_map`, `link_map`, `render_map`,
  `property_map`, `instance_version`, etc.
- `GET /api/1/rest/pipeline/{snode_id}/{version_number_int}` —
  pipeline definition for a specific past version. Note: server-side
  retention is limited — in our testing only v1 came back for a pipeline
  whose latest was `instance_version=7`; v2..v9 all 404. Use
  `/pipeline/versions/{snode_id}` (below) to learn which version numbers
  actually exist.
- `GET /api/1/rest/pipeline/versions/{snode_id}` — **per-checkpoint
  version metadata** (verb-before-id, not `/{snode_id}/versions`). Returns
  a list of `{version_number, asset_id, creator, time_created,
  version_tag, version_note}`. `version_note` is the free-text comment
  the user types in the Designer "Versions" dialog when checkpointing —
  the standard place for a bonus-question answer. Empty list when the
  pipeline has no checkpointed versions (autosaves don't appear). Used by
  `SnapLogicClient.get_pipeline_versions(snode_id)` and surfaced to the
  AI judge as `student_version_notes` in `ai_context.json`.
- `GET /api/1/rest/slfs/{org}/{ps}/{project}/{file_name}` — download a
  file asset from SLDB. Requires `Accept: */*` (the client's default
  `Accept: application/json` triggers a 406).

## Endpoint-naming gotcha (verb-before-id)

The version-list endpoint took an embarrassing amount of probing to find:
it's `/api/1/rest/pipeline/versions/{snode_id}` (verb path segment, then
id), not `/api/1/rest/pipeline/{snode_id}/versions`. The latter returns
400, and so do all sibling shapes (`/history`, `/checkin`, `/notes`,
`/log`, `/changelog`, etc.). Likewise, `/api/1/rest/pipeline/export/...`
is NOT a separate export endpoint — it's `/{snode_id}/{N}` with
`"export"` being parsed as the snode_id position and the next segment as
`version_number`. **Lesson**: before probing dozens of `/{id}/{suffix}`
shapes, try `/{verb}/{id}`. When in doubt, open SnapLogic Designer in a
browser, open DevTools → Network → XHR, and watch what the JS app calls
when you click the relevant UI control — that's how this one was found.

## What does NOT work — already probed, don't retry blindly

- `/api/1/rest/asset/{snode_id}` (snode_id only, no path) → 404. The
  `asset/...` endpoint requires the full `{org}/{ps}/{project}/{name}`
  path; it never accepts a bare snode_id.
- `/api/1/rest/asset/{path}?{expand,history,versions,full,verbose,detail,all}=...`
  → 200 but the param is silently ignored — response is identical to
  baseline.
- `/api/1/rest/asset/list/{path}?{include_history,asset_type=PipelineVersion,...}`
  → 200 but filters/expansions silently ignored.
- `/api/1/rest/{audit,snode,revision,revisions,tag,comment,notification,notes,version_notes,pipeline_notes,checkin_notes}/...`
  → 404.
- `/api/2/rest/...`, `/api/1/public/...`, `/api/1/public_api/...` → 404.

## Safety rule reminder

`SnapLogicClient` is GET-only by construction (see auto-memory:
`feedback_snaplogic_api_get_only`). Any future probe that needs POST /
PUT / DELETE requires explicit user approval first.
