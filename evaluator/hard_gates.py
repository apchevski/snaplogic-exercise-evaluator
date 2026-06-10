"""Deterministic pre-AI checks that can short-circuit an evaluation.

These are 'hard rules' — if any fail, the exercise is automatically
failed and we do NOT spend tokens asking the AI.

Current hard gates (see exercises/general_evaluation_rules.md):
  1. Student pipeline name must exactly match solution pipeline name.
  2a. (file_writer) Every student output file must exactly match the
      corresponding solution output file (rows, not byte order — we
      sort+compare so trivial encoding differences don't false-fail).
  2b. (triggered_task) A Triggered Task with the convention name
      (`<pipeline name> Task`) must exist in the student's project, AND
      every scenario's JSON response must match the cached expected
      response structurally.
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .name_match import names_match


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


def check_pipeline_name_match(solution_name: str, student_name: str) -> GateResult:
    """Compare pipeline names. Exact match except dash glyphs are interchangeable.

    See `evaluator.name_match`: en-dash / em-dash / hyphen-minus all count
    as the same character. Everything else (case, spacing, punctuation)
    must match exactly.
    """
    passed = names_match(solution_name, student_name)
    if passed:
        if solution_name == student_name:
            detail = f"Pipeline name matches: {solution_name!r}"
        else:
            detail = (
                f"Pipeline name matches (dash glyph differs): "
                f"solution={solution_name!r}, student={student_name!r}"
            )
    else:
        detail = (
            f"Pipeline name mismatch — solution={solution_name!r}, "
            f"student={student_name!r}"
        )
    return GateResult(name="pipeline_name_match", passed=passed, detail=detail)


def _columns_match(exp_header: list[str], act_header: list[str]) -> bool:
    """True when both headers carry the same column names with the same
    multiplicity, regardless of order.

    Column order is never significant at the output gate; only a missing or
    extra column (or a change in column count) is a real schema difference.
    """
    return sorted(exp_header) == sorted(act_header)


def _column_diff_note(exp_header: list[str], act_header: list[str]) -> str:
    """Human-readable 'missing'/'unexpected' column summary for a mismatch.

    Returns "" when the only difference is order (so callers never print a
    misleading diff for a pure reordering).
    """
    exp_set, act_set = set(exp_header), set(act_header)
    missing = [c for c in exp_header if c not in act_set]
    extra = [c for c in act_header if c not in exp_set]
    parts = []
    if missing:
        parts.append(f"missing: {missing}")
    if extra:
        parts.append(f"unexpected: {extra}")
    return ("\n  " + "; ".join(parts)) if parts else ""


def _build_column_index_map(
    target_header: list[str], source_header: list[str]
) -> list[int] | None:
    """Map each target column to a source column position, by name.

    Returns ``index_map`` where ``index_map[i]`` is the position of the
    ``i``-th target column within ``source_header``. Returns ``None`` when
    the source has duplicate column names (the by-name mapping is then
    ambiguous), so the caller skips realignment and compares positionally.
    """
    if len(set(source_header)) != len(source_header):
        return None
    pos = {name: i for i, name in enumerate(source_header)}
    try:
        return [pos[name] for name in target_header]
    except KeyError:
        return None


def _reorder_row(row: tuple[str, ...], index_map: list[int]) -> tuple[str, ...]:
    """Reorder a row's cells into the target column order via ``index_map``.

    Out-of-range source positions (a row shorter than the header) yield ""
    so a ragged row never raises during realignment.
    """
    return tuple(row[src] if src < len(row) else "" for src in index_map)


def check_output_files_match(
    expected_file: Path,
    actual_file: Path,
    *,
    columns_only: bool = False,
) -> GateResult:
    """Compare two tabular outputs (CSV or XLSX), header-aware and
    column-order-insensitive.

    Column ORDER never matters at this gate. Two outputs are treated as
    having matching columns when they carry the same column names with the
    same multiplicity, in any order — so a student who emits ``C, A, B``
    instead of ``A, B, C`` is not penalized here. Only a genuine schema
    difference (a missing column, an extra column, or a different column
    count) fails the column check.

    Default (``columns_only=False``): the two files must have the same
    column-name set AND the same row multiset. When the student's columns
    are in a different order, their rows are realigned to the expected
    column order (matched by name) before the multiset is compared, so a
    reordering does not produce a spurious row mismatch. Row order does NOT
    matter — pipeline ordering (sort/filter placement) is judged by the AI
    on the pipeline structure, not re-checked here.

    ``columns_only=True``: only the column-name set is compared; row data is
    ignored. For exercises whose output is inherently non-deterministic
    (e.g. an API that returns random rows every run) the rows can never
    match, but the column schema still must. The gate keeps the
    ``output_match`` name in both modes, so a column mismatch still routes
    to the AI judge for partial credit exactly like a row mismatch.

    Both modes read in a format-aware way: SnapLogic's Excel Formatter emits
    real ``.xlsx`` (a zip of XML), parsed for its worksheet rows; everything
    else is read as CSV.
    """
    if not expected_file.exists():
        return GateResult(
            name="output_match",
            passed=False,
            detail=f"Expected output not found at {expected_file}",
        )
    if not actual_file.exists():
        return GateResult(
            name="output_match",
            passed=False,
            detail=f"Student output not found at {actual_file}",
        )

    if columns_only:
        exp_header = _read_header(expected_file)
        act_header = _read_header(actual_file)
        if not _columns_match(exp_header, act_header):
            return GateResult(
                name="output_match",
                passed=False,
                detail=(
                    "Output columns differ. Row data is intentionally not "
                    "compared for this exercise (the output rows are "
                    "non-deterministic).\n"
                    f"  expected columns: {exp_header}\n"
                    f"  actual columns:   {act_header}"
                    f"{_column_diff_note(exp_header, act_header)}"
                ),
            )
        order_note = (
            " (column order differs; ignored)" if exp_header != act_header else ""
        )
        return GateResult(
            name="output_match",
            passed=True,
            detail=(
                f"Output columns match ({len(exp_header)} columns): "
                f"{exp_header}{order_note}. "
                f"Row data not compared (non-deterministic output)."
            ),
        )

    exp_header, exp_rows = _read_tabular(expected_file)
    act_header, act_rows = _read_tabular(actual_file)

    if not _columns_match(exp_header, act_header):
        return GateResult(
            name="output_match",
            passed=False,
            detail=(
                f"Output header mismatch.\n  expected: {exp_header}\n"
                f"  actual:   {act_header}"
                f"{_column_diff_note(exp_header, act_header)}"
            ),
        )

    # Same column set. If the student's columns are in a different order,
    # realign their rows to the expected column order (matched by name) so
    # that column order is irrelevant to the row-multiset comparison below.
    order_note = ""
    if exp_header != act_header:
        index_map = _build_column_index_map(exp_header, act_header)
        if index_map is not None:
            act_rows = [_reorder_row(r, index_map) for r in act_rows]
            order_note = " (student column order differed; realigned by name)"

    exp_sorted = sorted(exp_rows)
    act_sorted = sorted(act_rows)
    if exp_sorted != act_sorted:
        only_exp = [r for r in exp_sorted if r not in act_sorted][:5]
        only_act = [r for r in act_sorted if r not in exp_sorted][:5]
        return GateResult(
            name="output_match",
            passed=False,
            detail=(
                f"Output row contents differ. "
                f"expected_rows={len(exp_rows)}, actual_rows={len(act_rows)}. "
                f"Sample rows only in expected: {only_exp}. "
                f"Sample rows only in actual: {only_act}."
            ),
        )

    return GateResult(
        name="output_match",
        passed=True,
        detail=(
            f"Output matches ({len(exp_rows)} rows, {len(exp_header)} columns)"
            f"{order_note}."
        ),
    )


def check_output_files_match_multi(
    files: list[tuple[str, Path, Path]],
    *,
    columns_only: bool = False,
) -> GateResult:
    """Aggregate per-file output comparisons into one ``output_match`` gate.

    ``files`` is a list of ``(filename, expected_path, actual_path)``. Each
    entry is compared with :func:`check_output_files_match`; the gate passes
    iff **every** file passes. The detail lists each file's PASS/FAIL (with
    its reason) so the AI bundle can see exactly which report diverged.

    The gate keeps the ``output_match`` name regardless of file count, so
    a multi-output failure routes to the AI judge for partial credit exactly
    like a single-output failure. A length-1 ``files`` list reproduces the
    original single-output detail verbatim, so existing reports are unchanged.
    """
    if not files:
        return GateResult(
            name="output_match",
            passed=False,
            detail="No expected output files registered for this exercise.",
        )

    results = [
        (label, check_output_files_match(expected, actual, columns_only=columns_only))
        for label, expected, actual in files
    ]
    passed = all(r.passed for _, r in results)

    if len(results) == 1:
        # Preserve the original single-file detail verbatim.
        return GateResult(name="output_match", passed=passed, detail=results[0][1].detail)

    n_pass = sum(1 for _, r in results if r.passed)
    lines = [
        f"  [{'PASS' if r.passed else 'FAIL'}] {label}: {r.detail}"
        for label, r in results
    ]
    detail = f"{n_pass}/{len(results)} output file(s) match.\n" + "\n".join(lines)
    return GateResult(name="output_match", passed=passed, detail=detail)


def check_triggered_task_exists(
    expected_task_name: str,
    found_entry: dict[str, Any] | None,
) -> GateResult:
    """Verify the student created a Triggered Task with the expected name.

    Convention: a triggered task's name MUST be `<pipeline name> Task`.
    The convention is strict — a Triggered Task with any other name is
    treated as not-found and fails this gate, even if it correctly
    references the student's pipeline. See task_02_calculator/notes.md.
    """
    if found_entry is None:
        return GateResult(
            name="triggered_task_exists",
            passed=False,
            detail=(
                f"No Triggered Task named {expected_task_name!r} in the "
                f"student's project. The convention `<pipeline name> Task` "
                f"is strict — name the task exactly this."
            ),
        )
    return GateResult(
        name="triggered_task_exists",
        passed=True,
        detail=f"Triggered Task {expected_task_name!r} found.",
    )


def check_triggered_responses_match(
    scenarios: list[dict[str, Any]],
) -> GateResult:
    """Compare every scenario's expected response against the student's.

    `scenarios` is a list of dicts with keys:
      - name: scenario id (filesystem-safe)
      - expected_path: Path to cached solution response
      - student_path: Path to invoked student response
      - student_http_status: int | None
      - student_error: str | None

    Each scenario passes iff (a) the student invocation succeeded
    (status 200, no error) AND (b) the response body parses to JSON
    structurally equal to the expected body. Falls back to byte-equal
    when either side isn't valid JSON.

    Passes only when every scenario passes. Detail names each failing
    scenario and includes a short reason.
    """
    if not scenarios:
        return GateResult(
            name="triggered_task_responses_match",
            passed=False,
            detail="No scenarios registered in task.json.",
        )

    failures: list[str] = []
    for s in scenarios:
        name = s["name"]
        expected_path: Path = s["expected_path"]
        student_path: Path = s["student_path"]
        student_error = s.get("student_error")
        student_status = s.get("student_http_status")

        if student_error:
            failures.append(
                f"{name}: invocation failed ({student_error})"
            )
            continue
        if not expected_path.exists():
            failures.append(
                f"{name}: expected response missing at {expected_path}"
            )
            continue
        if not student_path.exists():
            failures.append(
                f"{name}: student response missing at {student_path}"
            )
            continue

        expected_bytes = expected_path.read_bytes()
        student_bytes = student_path.read_bytes()

        try:
            expected_json = json.loads(expected_bytes)
            student_json = json.loads(student_bytes)
            equal = expected_json == student_json
        except json.JSONDecodeError:
            equal = expected_bytes == student_bytes

        if not equal:
            preview_exp = _short_preview(expected_bytes)
            preview_act = _short_preview(student_bytes)
            failures.append(
                f"{name}: response differs — "
                f"expected={preview_exp} actual={preview_act}"
                + (f" (status {student_status})" if student_status not in (None, 200) else "")
            )

    if failures:
        return GateResult(
            name="triggered_task_responses_match",
            passed=False,
            detail=(
                f"{len(failures)} of {len(scenarios)} scenario(s) failed: "
                + " | ".join(failures)
            ),
        )

    return GateResult(
        name="triggered_task_responses_match",
        passed=True,
        detail=f"All {len(scenarios)} scenario response(s) match expected.",
    )


def _short_preview(data: bytes, max_chars: int = 120) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(data)} bytes, non-utf8>"
    text = text.replace("\n", "\\n").replace("\r", "\\r")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return repr(text)


def _read_csv(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    body = [tuple(r) for r in rows[1:]]
    return header, body


def _read_tabular(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    """Read a CSV or XLSX output into ``(header, body)`` for the exact-match gate.

    Sniffs the zip magic the same way :func:`_read_header` does so the
    row-multiset comparison handles SnapLogic's Excel Formatter output
    (a zip of XML) instead of crashing on the binary as if it were UTF-8 CSV.
    """
    with path.open("rb") as fh:
        magic = fh.read(4)
    if magic == b"PK\x03\x04":
        return _read_xlsx_rows(path)
    return _read_csv(path)


# --- Format-aware header extraction (CSV or XLSX) ---------------------------

# SpreadsheetML namespace used throughout an .xlsx workbook's XML parts.
_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_T = f"{{{_XLSX_NS}}}t"
_XLSX_NS_MAP = {"x": _XLSX_NS}


def _read_header(path: Path) -> list[str]:
    """Return the column header row from a CSV or XLSX file.

    SnapLogic's Excel Formatter emits real ``.xlsx`` (a zip of XML), so we
    sniff the zip magic and parse the first worksheet row when we see it;
    everything else is read as CSV. Only the header is materialized, so
    this stays cheap even on large outputs.
    """
    with path.open("rb") as fh:
        magic = fh.read(4)
    if magic == b"PK\x03\x04":
        return _read_xlsx_header(path)
    header, _ = _read_csv(path)
    return header


def _read_xlsx_header(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        sheet_part = _xlsx_first_sheet_part(zf)
        if sheet_part is None:
            return []
        shared = _xlsx_shared_strings(zf)
        with zf.open(sheet_part) as fh:
            root = ET.parse(fh).getroot()
    sheet_data = root.find("x:sheetData", _XLSX_NS_MAP)
    if sheet_data is None:
        return []
    first_row = sheet_data.find("x:row", _XLSX_NS_MAP)
    if first_row is None:
        return []
    return [
        _xlsx_cell_text(c, shared)
        for c in first_row.findall("x:c", _XLSX_NS_MAP)
    ]


def _read_xlsx_rows(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    """Read a SnapLogic Excel Formatter ``.xlsx`` into ``(header, body)``.

    Mirrors :func:`_read_csv` for the exact-match gate. Cells are placed by
    their column reference (``r="B2"``) so sparse rows — where the formatter
    omits empty cells — stay column-aligned; gaps are filled with "". The
    first worksheet row is the header; the remaining rows are the body,
    each padded to the header width so trailing empties don't desync the
    multiset comparison between the two files.
    """
    with zipfile.ZipFile(path) as zf:
        sheet_part = _xlsx_first_sheet_part(zf)
        if sheet_part is None:
            return [], []
        shared = _xlsx_shared_strings(zf)
        with zf.open(sheet_part) as fh:
            root = ET.parse(fh).getroot()
    sheet_data = root.find("x:sheetData", _XLSX_NS_MAP)
    if sheet_data is None:
        return [], []
    rows: list[list[str]] = []
    for row_el in sheet_data.findall("x:row", _XLSX_NS_MAP):
        cells: dict[int, str] = {}
        pos = 0
        max_idx = -1
        for c in row_el.findall("x:c", _XLSX_NS_MAP):
            idx = _xlsx_col_index(c.get("r"))
            if idx is None:
                idx = pos
            cells[idx] = _xlsx_cell_text(c, shared)
            pos = idx + 1
            max_idx = max(max_idx, idx)
        rows.append([cells.get(i, "") for i in range(max_idx + 1)])
    if not rows:
        return [], []
    header = rows[0]
    width = len(header)
    body = [
        tuple(r + [""] * (width - len(r)) if len(r) < width else r)
        for r in rows[1:]
    ]
    return header, body


def _xlsx_col_index(ref: str | None) -> int | None:
    """Column index from a cell ref like ``"B2"`` → 1; None if unparseable."""
    if not ref:
        return None
    letters = "".join(ch for ch in ref if ch.isalpha())
    if not letters:
        return None
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _xlsx_first_sheet_part(zf: zipfile.ZipFile) -> str | None:
    """Pick the first worksheet part. SnapLogic writes xl/worksheets/sheet1.xml."""
    sheets = [
        n for n in zf.namelist()
        if n.startswith("xl/worksheets/") and n.endswith(".xml")
    ]
    if "xl/worksheets/sheet1.xml" in sheets:
        return "xl/worksheets/sheet1.xml"
    return min(sheets) if sheets else None


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """Resolve the shared-string table; empty when cells use inline strings."""
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            root = ET.parse(fh).getroot()
    except KeyError:
        return []
    return [_xlsx_text_join(si) for si in root.findall("x:si", _XLSX_NS_MAP)]


def _xlsx_cell_text(cell: ET.Element, shared: list[str]) -> str:
    """Extract a cell's display text, handling inline / shared / literal values."""
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        is_el = cell.find("x:is", _XLSX_NS_MAP)
        return _xlsx_text_join(is_el) if is_el is not None else ""
    v = cell.find("x:v", _XLSX_NS_MAP)
    if v is None or v.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    return v.text


def _xlsx_text_join(el: ET.Element) -> str:
    """Concatenate every <t> descendant — covers plain <t> and rich-text runs."""
    return "".join(t.text or "" for t in el.iter(_XLSX_T))
