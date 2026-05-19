"""GET-only HTTP client for the SnapLogic REST API.

Safety rule (auto-memory: feedback_snaplogic_api_get_only):
    No mutating HTTP methods. This client physically refuses anything
    other than GET, so a future bug can't accidentally mutate the org.

Endpoint shapes (validated against `elastic.snaplogic.com`):
    GET /api/1/rest/asset/list/{org}/{ps}/{project}
        -> response_map.entries[]  (each: name, snode_id, asset_type, path, ...)

    GET /api/1/rest/asset/{org}/{ps}/{project}/{name}
        -> response_map  (asset metadata; includes snode_id for pipelines)

    GET /api/1/rest/pipeline/{snode_id}
        -> response_map  (full pipeline definition: snap_map, link_map, ...)

    GET /api/1/rest/pipeline/versions/{snode_id}
        -> response_map  (LIST of per-checkpoint version records, each
                          with version_number, creator, time_created,
                          version_tag, version_note). The version_note
                          is the text shown in the Designer "Versions"
                          dialog — the only place students can put a
                          bonus answer that survives subsequent saves.
                          Note: verb-before-id; /pipeline/{snode_id}/versions
                          returns 400.

Notes:
- The Public-API `/catalog/...` endpoint is a paid feature; we don't use it.
- Authentication is HTTP Basic with admin credentials.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings


class SnapLogicClient:
    def __init__(self, settings: Settings, timeout_s: float = 30.0) -> None:
        self._settings = settings
        self._http = httpx.Client(
            base_url=settings.base_url,
            auth=(settings.username, settings.password),
            timeout=timeout_s,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SnapLogicClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- low-level -----

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        if not path.startswith("/"):
            path = "/" + path
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.get(path, params=params).json()

    def get_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        return self.get(path, params=params).content

    # ----- high-level helpers -----

    @staticmethod
    def _encode_path_segments(*parts: str) -> str:
        """Percent-encode each segment but keep slashes between segments."""
        return "/".join(quote(p, safe="") for p in parts if p)

    def list_assets(
        self, org: str, project_space: str, project: str
    ) -> list[dict[str, Any]]:
        """List every asset in a project. Returns `response_map.entries`."""
        path = "/api/1/rest/asset/list/" + self._encode_path_segments(
            org, project_space, project
        )
        data = self.get_json(path)
        return data["response_map"]["entries"]

    def find_pipeline_asset_entry(
        self,
        org: str,
        project_space: str,
        project: str,
        pipeline_name: str,
    ) -> dict[str, Any]:
        """Return the full asset-list entry for a pipeline by name.

        Useful for cache invalidation: callers can read the entry's
        modified-at timestamp (e.g. `update_time`) without fetching the
        full pipeline body.
        """
        for entry in self.list_assets(org, project_space, project):
            if entry.get("asset_type") == "Pipeline" and entry.get("name") == pipeline_name:
                return entry
        raise LookupError(
            f"No pipeline named {pipeline_name!r} in "
            f"{org}/{project_space}/{project}"
        )

    def find_pipeline_snode_id(
        self,
        org: str,
        project_space: str,
        project: str,
        pipeline_name: str,
    ) -> str:
        """Resolve a pipeline's name to its snode_id by listing the project."""
        return self.find_pipeline_asset_entry(org, project_space, project, pipeline_name)["snode_id"]

    def get_pipeline_definition(self, snode_id: str) -> dict[str, Any]:
        """Fetch the full pipeline definition by snode_id.

        Returns the inner `response_map` object directly (the SnapLogic
        API wraps everything in `{"response_map": {...}}`).
        """
        data = self.get_json(f"/api/1/rest/pipeline/{quote(snode_id, safe='')}")
        return data["response_map"]

    def get_pipeline_versions(self, snode_id: str) -> list[dict[str, Any]]:
        """List per-checkpoint version records for a pipeline.

        Each record: {version_number, asset_id, creator, time_created,
        version_tag, version_note}. The `version_note` is what students
        type into the SnapLogic Designer "Versions" dialog when saving
        a checkpoint — the standard place for a bonus-question answer.
        Returns an empty list when the pipeline has no checkpointed
        versions yet (autosaves don't count).
        """
        data = self.get_json(
            f"/api/1/rest/pipeline/versions/{quote(snode_id, safe='')}"
        )
        return data.get("response_map") or []

    def get_pipeline_by_path(
        self,
        org: str,
        project_space: str,
        project: str,
        pipeline_name: str,
    ) -> dict[str, Any]:
        """Convenience: resolve name → snode_id → fetch definition."""
        snode_id = self.find_pipeline_snode_id(org, project_space, project, pipeline_name)
        return self.get_pipeline_definition(snode_id)

    def download_sldb_file(
        self,
        org: str,
        project_space: str,
        project: str,
        file_name: str,
    ) -> bytes:
        """Download a file asset from SLDB by its path.

        Endpoint: `GET /api/1/rest/slfs/{org}/{ps}/{project}/{file}` with
        `Accept: */*` (the default `application/json` triggers a 406).
        Returns the raw file bytes.
        """
        path = "/api/1/rest/slfs/" + self._encode_path_segments(
            org, project_space, project, file_name
        )
        resp = self._http.get(path, headers={"Accept": "*/*"})
        resp.raise_for_status()
        return resp.content
