"""Dash-tolerant exact-name matching for pipelines and Triggered Tasks.

Rule: pipeline / triggered-task / pipeline-path names must match exactly
EXCEPT that the three dash glyphs used interchangeably in SnapLogic
Designer compare as equal:

    HYPHEN-MINUS  U+002D  '-'
    EN DASH       U+2013  '–'
    EM DASH       U+2014  '—'

So `Task 03 – Join Employee Records` (en dash) and `Task 03 - Join
Employee Records` (hyphen) are the same name. Everything else (case,
spacing, accents, other punctuation) remains strict — only the dash
glyph is normalized.

Use `names_match(a, b)` for the boolean comparison and `normalize_name`
when a canonical form is needed (sort keys, dedup, etc.).

The same rule applies to full pipeline paths
(`Org/ProjectSpace/Project/PipelineName`) via `pipeline_paths_match` —
only the trailing pipeline-name segment can vary by dash glyph; the
other segments still compare exactly.
"""
from __future__ import annotations

_DASH_EQUIVALENTS = ("–", "—")  # en dash, em dash -> hyphen-minus


def normalize_name(name: str) -> str:
    """Return `name` with en-dash / em-dash collapsed to hyphen-minus."""
    out = name
    for d in _DASH_EQUIVALENTS:
        out = out.replace(d, "-")
    return out


def names_match(a: str, b: str) -> bool:
    """Exact match modulo dash glyph (en/em-dash count as hyphen-minus)."""
    return normalize_name(a) == normalize_name(b)


def pipeline_paths_match(a: str, b: str) -> bool:
    """Same as `names_match`, applied segment-by-segment to a pipeline path.

    A pipeline path is `Org/ProjectSpace/Project/PipelineName`. Project
    spaces and project folders also occasionally contain dashes, so we
    apply the same normalization to every segment rather than only the
    last one.
    """
    return normalize_name(a) == normalize_name(b)
