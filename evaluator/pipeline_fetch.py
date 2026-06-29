"""Fetch pipeline definitions + output files from SnapLogic.

A "pipeline location" is a 4-tuple: (org, project_space, project, name).
Org is fixed per evaluation; the other three vary between solution and
student.

The output file is fetched from SLDB via the project's path:
  /api/1/rest/slfs/{org}/{ps}/{project}/{file_name}

This assumes the File Writer snap actually executed and wrote to the
project's SLDB folder. If a future exercise writes to a subdirectory,
pass `file_name` like 'subdir/output.csv'.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .snaplogic_client import SnapLogicClient
from .tasks import TriggeredRequest


class SolutionNotReadyError(Exception):
    """Raised by load_cached_solution_pipeline when the cache is missing or stale.

    Carries a short `status` slug (e.g. `missing_solution_json`,
    `stale_signature`) for downstream code to surface in manifests, plus
    a human-readable `reason`.
    """

    def __init__(self, status: str, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(reason)


SOLUTION_SIGNATURE_KEYS: tuple[str, ...] = (
    # `time_updated` is what `/api/1/rest/asset/list/...` actually returns
    # for pipelines (verified 2026-05-19 against elastic.snaplogic.com).
    # The other keys are defensive fallbacks in case SnapLogic ever renames it.
    "time_updated",
    "update_time",
    "modified_at",
    "updated_at",
    "last_modified",
    "modify_time",
)


@dataclass(frozen=True)
class PipelineLocation:
    org: str
    project_space: str
    project: str
    name: str

    @classmethod
    def from_path(cls, full_path: str) -> "PipelineLocation":
        """Parse 'Org/ProjectSpace/Project/PipelineName' into a location."""
        parts = [p for p in full_path.strip().split("/") if p]
        if len(parts) != 4:
            raise ValueError(
                f"Expected 'Org/ProjectSpace/Project/PipelineName', got: {full_path!r}"
            )
        return cls(*parts)


@dataclass
class FetchedPipeline:
    location: PipelineLocation
    definition: dict[str, Any]
    raw_json_path: Path  # where we cached the raw API response


def fetch_pipeline(
    client: SnapLogicClient,
    loc: PipelineLocation,
    cache_dir: Path,
) -> FetchedPipeline:
    cache_dir.mkdir(parents=True, exist_ok=True)
    definition = client.get_pipeline_by_path(
        loc.org, loc.project_space, loc.project, loc.name
    )
    cache_path = cache_dir / f"{_safe_name(loc.name)}.pipeline.json"
    cache_path.write_text(json.dumps(definition, indent=2), encoding="utf-8")
    return FetchedPipeline(
        location=loc, definition=definition, raw_json_path=cache_path
    )


def fetch_pipeline_output_file(
    client: SnapLogicClient,
    loc: PipelineLocation,
    file_name: str,
    dest_path: Path,
) -> Path:
    """Download `file_name` from the project's SLDB folder to `dest_path`."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    data = client.download_sldb_file(
        loc.org, loc.project_space, loc.project, file_name
    )
    dest_path.write_bytes(data)
    return dest_path


def _extract_remote_signature(asset_entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return (kind, value) for the first usable timestamp field, else None.

    "Usable" = present and not None/empty/zero. Value is always coerced to
    str so cache comparisons don't trip on epoch-int vs iso-string drift.
    """
    for k in SOLUTION_SIGNATURE_KEYS:
        v = asset_entry.get(k)
        if v not in (None, "", 0):
            return (k, str(v))
    return None


def _content_signature(definition: dict[str, Any]) -> tuple[str, str]:
    """Fallback signature when the API doesn't expose a modified-at field.

    Hashes the canonical-JSON-serialized definition. Slow path: still
    requires a body fetch, but lets us skip rewriting unchanged caches.
    """
    canon = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return ("sha256", hashlib.sha256(canon).hexdigest())


def load_or_refresh_solution_pipeline(
    client: SnapLogicClient,
    loc: PipelineLocation,
    solution_json_path: Path,
    sidecar_path: Path,
    *,
    expected_dir: Path,
    output_filenames: tuple[str, ...],
    force_refresh: bool = False,
) -> tuple[FetchedPipeline, bool]:
    """Load the cached solution pipeline JSON, refetching only when stale.

    Returns (fetched, refreshed). When refreshed=True we also rewrote every
    expected output file — solution-pipeline changes typically imply output changes,
    so the caches are invalidated atomically. A single-output exercise is
    just a length-1 ``output_filenames``.

    Cache check (skipped if force_refresh):
      1. Probe `client.list_assets(...)` for the solution pipeline's entry.
      2. Extract a remote signature (timestamp field, fallback chain).
      3. If `solution.json` + sidecar + every expected output file exist and the
         sidecar (kind, value) match the remote, load from disk and return
         (refreshed=False).
      4. Otherwise fetch the full body, rewrite both files, rewrite every
         expected output file, return (refreshed=True).
    """
    if force_refresh:
        entry = client.find_pipeline_asset_entry(
            loc.org, loc.project_space, loc.project, loc.name
        )
        return _do_refresh(
            client, loc, entry, solution_json_path, sidecar_path,
            expected_dir, output_filenames,
        )

    entry = client.find_pipeline_asset_entry(
        loc.org, loc.project_space, loc.project, loc.name
    )
    remote_sig = _extract_remote_signature(entry)

    expected_paths = [expected_dir / f for f in output_filenames]
    all_expected_present = all(p.exists() for p in expected_paths)

    if (
        remote_sig is not None
        and solution_json_path.exists()
        and sidecar_path.exists()
        and all_expected_present
    ):
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sidecar = None
        if (
            sidecar is not None
            and sidecar.get("signature_kind") == remote_sig[0]
            and sidecar.get("signature") == remote_sig[1]
        ):
            definition = json.loads(solution_json_path.read_text(encoding="utf-8"))
            return (
                FetchedPipeline(
                    location=loc,
                    definition=definition,
                    raw_json_path=solution_json_path,
                ),
                False,
            )

    return _do_refresh(
        client, loc, entry, solution_json_path, sidecar_path,
        expected_dir, output_filenames,
    )


def load_cached_solution_pipeline(
    client: SnapLogicClient,
    loc: PipelineLocation,
    solution_json_path: Path,
    sidecar_path: Path,
    expected_dir: Path,
    output_filenames: tuple[str, ...],
) -> FetchedPipeline:
    """Strict read of the cached solution. No writes.

    Hits the remote ONCE to read the asset-list timestamp, then compares
    it against the sidecar signature. Raises SolutionNotReadyError if
    any cached file is missing (including any expected output file) or the cache
    is stale. Callers that get this exception should surface a `needs_prep`
    outcome and stop — refreshing the cache is /prep's job.
    """
    if not solution_json_path.exists():
        raise SolutionNotReadyError(
            "missing_solution_json",
            f"Cached solution.json not found at {solution_json_path}",
        )
    if not sidecar_path.exists():
        raise SolutionNotReadyError(
            "missing_sidecar",
            f"Cached sidecar not found at {sidecar_path}",
        )
    for filename in output_filenames:
        expected_output_path = expected_dir / filename
        if not expected_output_path.exists():
            raise SolutionNotReadyError(
                "missing_expected_output",
                f"Cached expected output file not found at {expected_output_path}",
            )

    try:
        entry = client.find_pipeline_asset_entry(
            loc.org, loc.project_space, loc.project, loc.name
        )
    except LookupError as e:
        raise SolutionNotReadyError("pipeline_not_found", str(e)) from e

    remote_sig = _extract_remote_signature(entry)

    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SolutionNotReadyError(
            "corrupt_sidecar",
            f"Unreadable sidecar at {sidecar_path}: {e}",
        ) from e

    if remote_sig is not None:
        sidecar_sig = (sidecar.get("signature_kind"), sidecar.get("signature"))
        if sidecar_sig != remote_sig:
            raise SolutionNotReadyError(
                "stale_signature",
                (
                    f"Solution cache is stale for {loc.name!r}: "
                    f"sidecar={sidecar_sig}, remote={remote_sig}"
                ),
            )

    definition = json.loads(solution_json_path.read_text(encoding="utf-8"))
    return FetchedPipeline(
        location=loc,
        definition=definition,
        raw_json_path=solution_json_path,
    )


def load_cached_solution_triggered_task(
    client: SnapLogicClient,
    loc: PipelineLocation,
    solution_json_path: Path,
    sidecar_path: Path,
    *,
    expected_dir: Path,
    requests: tuple[TriggeredRequest, ...],
) -> FetchedPipeline:
    """Strict read of the cached triggered-task solution. No writes.

    Triggered-task analog of `load_cached_solution_pipeline`. Hits the
    remote ONCE to read the asset-list timestamp, then compares it
    against the sidecar signature. Raises SolutionNotReadyError if any
    cached file is missing or the cache is stale. Callers that get this
    exception should surface a `needs_prep` outcome and stop —
    refreshing the cache is /prep's job.
    """
    if not solution_json_path.exists():
        raise SolutionNotReadyError(
            "missing_solution_json",
            f"Cached solution.json not found at {solution_json_path}",
        )
    if not sidecar_path.exists():
        raise SolutionNotReadyError(
            "missing_sidecar",
            f"Cached sidecar not found at {sidecar_path}",
        )
    for req in requests:
        expected_path = expected_dir / f"{req.name}.json"
        if not expected_path.exists():
            raise SolutionNotReadyError(
                "missing_expected_response",
                f"Cached expected response not found at {expected_path}",
            )

    try:
        entry = client.find_pipeline_asset_entry(
            loc.org, loc.project_space, loc.project, loc.name
        )
    except LookupError as e:
        raise SolutionNotReadyError("pipeline_not_found", str(e)) from e

    remote_sig = _extract_remote_signature(entry)

    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SolutionNotReadyError(
            "corrupt_sidecar",
            f"Unreadable sidecar at {sidecar_path}: {e}",
        ) from e

    if remote_sig is not None:
        sidecar_sig = (sidecar.get("signature_kind"), sidecar.get("signature"))
        if sidecar_sig != remote_sig:
            raise SolutionNotReadyError(
                "stale_signature",
                (
                    f"Solution cache is stale for {loc.name!r}: "
                    f"sidecar={sidecar_sig}, remote={remote_sig}"
                ),
            )

    definition = json.loads(solution_json_path.read_text(encoding="utf-8"))
    return FetchedPipeline(
        location=loc,
        definition=definition,
        raw_json_path=solution_json_path,
    )


def fetch_student_triggered_responses(
    client: SnapLogicClient,
    loc: PipelineLocation,
    triggered_task_name: str,
    requests: tuple[TriggeredRequest, ...],
    dest_dir: Path,
) -> dict[str, tuple[Path, int | None, str | None]]:
    """Invoke the student's Triggered Task once per scenario.

    Writes each response body to `dest_dir/<request_name>.json` verbatim
    (no reformatting). Returns a mapping `{request_name: (path, http_status, error)}`
    where `error` is None on success or a short string on failure (in
    which case `path` is still where the body — possibly an error body —
    was written, and `http_status` is the response code or None if the
    request didn't reach the server).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[Path, int | None, str | None]] = {}
    for req in requests:
        out_path = dest_dir / f"{req.name}.json"
        try:
            body = client.invoke_triggered_task(
                loc.org, loc.project_space, loc.project,
                triggered_task_name,
                params=req.params,
            )
            out_path.write_bytes(body)
            results[req.name] = (out_path, 200, None)
        except httpx.HTTPStatusError as e:
            # Write the error body so the AI / report can see what
            # SnapLogic actually returned (often a JSON error envelope).
            try:
                out_path.write_bytes(e.response.content)
            except OSError:
                pass
            results[req.name] = (
                out_path,
                e.response.status_code,
                f"HTTP {e.response.status_code}",
            )
        except httpx.RequestError as e:
            results[req.name] = (out_path, None, f"request error: {e!s}")
    return results


def load_or_refresh_solution_triggered_task(
    client: SnapLogicClient,
    loc: PipelineLocation,
    solution_json_path: Path,
    sidecar_path: Path,
    *,
    expected_dir: Path,
    triggered_task_name: str,
    requests: tuple[TriggeredRequest, ...],
    force_refresh: bool = False,
) -> tuple[FetchedPipeline, bool]:
    """Triggered-task analog of `load_or_refresh_solution_pipeline`.

    Reuses the same signature-based cache (solution.json + sidecar
    keyed off the pipeline asset's `time_updated`) but refreshes
    `expected/<request_name>.json` instead of output files. The
    Triggered Task is invoked once per request when a refresh fires;
    each response body is written to expected/ verbatim.

    Returns (fetched, refreshed). When refreshed=True every expected
    JSON was rewritten — pipeline definition changes typically imply
    response changes, so the caches are invalidated atomically.
    """
    if force_refresh:
        entry = client.find_pipeline_asset_entry(
            loc.org, loc.project_space, loc.project, loc.name
        )
        return _do_refresh_triggered(
            client, loc, entry, solution_json_path, sidecar_path,
            expected_dir, triggered_task_name, requests,
        )

    entry = client.find_pipeline_asset_entry(
        loc.org, loc.project_space, loc.project, loc.name
    )
    remote_sig = _extract_remote_signature(entry)

    expected_paths = [expected_dir / f"{r.name}.json" for r in requests]
    all_expected_present = all(p.exists() for p in expected_paths)

    if (
        remote_sig is not None
        and solution_json_path.exists()
        and sidecar_path.exists()
        and all_expected_present
    ):
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sidecar = None
        if (
            sidecar is not None
            and sidecar.get("signature_kind") == remote_sig[0]
            and sidecar.get("signature") == remote_sig[1]
        ):
            definition = json.loads(solution_json_path.read_text(encoding="utf-8"))
            return (
                FetchedPipeline(
                    location=loc,
                    definition=definition,
                    raw_json_path=solution_json_path,
                ),
                False,
            )

    return _do_refresh_triggered(
        client, loc, entry, solution_json_path, sidecar_path,
        expected_dir, triggered_task_name, requests,
    )


def _do_refresh_triggered(
    client: SnapLogicClient,
    loc: PipelineLocation,
    asset_entry: dict[str, Any],
    solution_json_path: Path,
    sidecar_path: Path,
    expected_dir: Path,
    triggered_task_name: str,
    requests: tuple[TriggeredRequest, ...],
) -> tuple[FetchedPipeline, bool]:
    snode_id = asset_entry["snode_id"]
    definition = client.get_pipeline_definition(snode_id)

    sig = _extract_remote_signature(asset_entry) or _content_signature(definition)

    solution_json_path.parent.mkdir(parents=True, exist_ok=True)
    solution_json_path.write_text(json.dumps(definition, indent=2), encoding="utf-8")

    sidecar = {
        "signature_kind": sig[0],
        "signature": sig[1],
        "snode_id": snode_id,
        "pipeline_name": loc.name,
        "triggered_task_name": triggered_task_name,
        "cached_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    expected_dir.mkdir(parents=True, exist_ok=True)
    for req in requests:
        body = client.invoke_triggered_task(
            loc.org, loc.project_space, loc.project,
            triggered_task_name,
            params=req.params,
        )
        out_path = expected_dir / f"{req.name}.json"
        # The triggered-task feed always serves JSON for these exercises,
        # but we write bytes verbatim so we never reformat / re-encode
        # the response. Comparisons in /grade use structural JSON diff.
        out_path.write_bytes(body)

    return (
        FetchedPipeline(
            location=loc,
            definition=definition,
            raw_json_path=solution_json_path,
        ),
        True,
    )


def extract_binary_write_filenames(definition: dict[str, Any]) -> list[str]:
    """Return every `filename` value from `com-snaplogic-snaps-binary-write` snaps.

    Used by /prep to auto-detect a task's expected output filename. The
    list preserves snap_map iteration order (which is not execution
    order; we don't need execution order here — the writer set is
    unordered by intent).
    """
    out: list[str] = []
    snap_map = definition.get("snap_map", {}) or {}
    for snap in snap_map.values():
        if snap.get("class_id") != "com-snaplogic-snaps-binary-write":
            continue
        settings = (snap.get("property_map") or {}).get("settings") or {}
        filename = (settings.get("filename") or {}).get("value")
        if isinstance(filename, str) and filename:
            out.append(filename)
    return out


def _do_refresh(
    client: SnapLogicClient,
    loc: PipelineLocation,
    asset_entry: dict[str, Any],
    solution_json_path: Path,
    sidecar_path: Path,
    expected_dir: Path,
    output_filenames: tuple[str, ...],
) -> tuple[FetchedPipeline, bool]:
    snode_id = asset_entry["snode_id"]
    definition = client.get_pipeline_definition(snode_id)

    sig = _extract_remote_signature(asset_entry) or _content_signature(definition)

    solution_json_path.parent.mkdir(parents=True, exist_ok=True)
    solution_json_path.write_text(json.dumps(definition, indent=2), encoding="utf-8")

    sidecar = {
        "signature_kind": sig[0],
        "signature": sig[1],
        "snode_id": snode_id,
        "pipeline_name": loc.name,
        "cached_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    for filename in output_filenames:
        fetch_pipeline_output_file(client, loc, filename, expected_dir / filename)

    return (
        FetchedPipeline(
            location=loc,
            definition=definition,
            raw_json_path=solution_json_path,
        ),
        True,
    )


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def flow_order(pipeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the snap dicts in true topological execution order.

    SnapLogic stores snaps in `snap_map` as a UUID-keyed dict. Dict
    iteration order is NOT execution order — it's typically insertion
    order from the Designer and breaks as soon as the user reorders
    snaps. The real flow is encoded in `link_map`. We Kahn-topo-sort.
    Branches and parallel views are flattened in topological order
    (callers that need branch awareness should walk `link_map` directly).
    """
    snap_map: dict[str, Any] = pipeline.get("snap_map", {}) or {}
    link_map: dict[str, Any] = pipeline.get("link_map", {}) or {}

    edges_out: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {sid: 0 for sid in snap_map}
    seen: set[tuple[str, str]] = set()
    for link in link_map.values():
        src = link.get("src_id")
        dst = link.get("dst_id")
        if not (src and dst) or (src, dst) in seen:
            continue
        seen.add((src, dst))
        edges_out[src].append(dst)
        if dst in indeg:
            indeg[dst] += 1

    queue = deque(sid for sid, d in indeg.items() if d == 0)
    ordered_ids: list[str] = []
    while queue:
        sid = queue.popleft()
        ordered_ids.append(sid)
        for nxt in edges_out[sid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    # Append any snaps not reached (cycles or orphans — shouldn't happen
    # in valid SnapLogic pipelines, but don't drop them silently).
    for sid in snap_map:
        if sid not in ordered_ids:
            ordered_ids.append(sid)

    return [snap_map[sid] for sid in ordered_ids if sid in snap_map]


def snap_label(snap: dict[str, Any]) -> str:
    label = (
        ((snap.get("property_map") or {}).get("info") or {}).get("label") or {}
    ).get("value")
    return label or snap.get("class_fqid", "?")


def flow_order_summary(pipeline: dict[str, Any]) -> list[dict[str, str]]:
    """Compact flow-order summary suitable for prompting an LLM."""
    return [
        {"class_fqid": s.get("class_fqid", "?"), "label": snap_label(s)}
        for s in flow_order(pipeline)
    ]
