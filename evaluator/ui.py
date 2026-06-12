"""Static-HTML grade dashboard generator.

Reads every `grades/<student>/report.json`, embeds the data into a single
self-contained `ui/index.html` (inline CSS + JS + JSON), and opens it in
the default browser. The generated file works under file:// — no HTTP
server, no `fetch` calls, double-clickable.

    python -m evaluator.ui              # build + open in browser
    python -m evaluator.ui --no-open    # build only

The UI reflects the points-based grading system: each student card
shows a `Total: X/Y points` badge plus verdict counts; each task's
header shows `points/10` and the differences list is split into
Deductions (point cost) and Notes (mention-only).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import webbrowser
from typing import Any

from .config import GRADES_DIR, REPO_ROOT

UI_DIR = REPO_ROOT / "ui"
UI_INDEX = UI_DIR / "index.html"


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SnapLogic Exercise Grades</title>
<style>
*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: #f8fafc;
  color: #1f2937;
  margin: 0;
  line-height: 1.55;
  font-size: 15px;
}
.site-header {
  background: white;
  border-bottom: 1px solid #e5e7eb;
  padding: 1.75rem 2rem 1.25rem;
}
.site-header h1 {
  margin: 0 0 0.25rem;
  font-size: 1.625rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.site-header .meta {
  color: #6b7280;
  font-size: 0.875rem;
}
.site-header .meta .dot { margin: 0 0.5rem; opacity: 0.5; }
.controls {
  display: flex;
  gap: 0.75rem;
  padding: 1rem 2rem;
  background: white;
  border-bottom: 1px solid #e5e7eb;
  position: sticky;
  top: 0;
  z-index: 10;
  flex-wrap: wrap;
}
.controls input, .controls select {
  padding: 0.5rem 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: white;
  font: inherit;
  color: inherit;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.controls input:focus, .controls select:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59,130,246,0.15);
}
.controls input { flex: 1; min-width: 220px; }
main {
  padding: 1.5rem 2rem 3rem;
  max-width: 1100px;
  margin: 0 auto;
}
.student-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 1.5rem;
  margin-bottom: 1rem;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.student-card > header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
.student-card h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}
.student-card > header .meta {
  color: #6b7280;
  font-size: 0.875rem;
}
.student-card > header .meta .ps {
  background: #f3f4f6;
  padding: 0.125rem 0.5rem;
  border-radius: 4px;
  font-family: ui-monospace, "Cascadia Mono", "Fira Code", Menlo, monospace;
  font-size: 0.8125rem;
}
.total-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
.total-badge {
  display: inline-flex;
  align-items: baseline;
  gap: 0.375rem;
  padding: 0.375rem 0.875rem;
  border-radius: 8px;
  background: #eef2ff;
  color: #3730a3;
  font-weight: 600;
  font-size: 0.9375rem;
  border: 1px solid #c7d2fe;
}
.total-badge .pct {
  font-size: 0.8125rem;
  font-weight: 500;
  color: #4338ca;
  opacity: 0.85;
}
.total-badge.tier-high { background: #d1fae5; color: #065f46; border-color: #6ee7b7; }
.total-badge.tier-high .pct { color: #047857; }
.total-badge.tier-mid  { background: #fef3c7; color: #78350f; border-color: #fcd34d; }
.total-badge.tier-mid  .pct { color: #92400e; }
.total-badge.tier-low  { background: #fee2e2; color: #7f1d1d; border-color: #fca5a5; }
.total-badge.tier-low  .pct { color: #991b1b; }
.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.badge {
  padding: 0.25rem 0.625rem;
  border-radius: 999px;
  font-size: 0.8125rem;
  font-weight: 500;
}
.badge.pass { background: #d1fae5; color: #047857; }
.badge.fail { background: #fee2e2; color: #991b1b; }
.badge.missing { background: #f3f4f6; color: #374151; }
.badge.needs-prep { background: #dbeafe; color: #1e40af; }
.overall {
  margin: 0 0 1rem;
  padding: 0.875rem 1rem;
  background: #f9fafb;
  border-left: 3px solid #3b82f6;
  border-radius: 4px;
  color: #374151;
}
details {
  border-top: 1px solid #e5e7eb;
  padding-top: 1rem;
}
details summary {
  cursor: pointer;
  user-select: none;
  color: #3b82f6;
  font-weight: 500;
  font-size: 0.9375rem;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}
details summary::-webkit-details-marker { display: none; }
details summary::before {
  content: "▸";
  font-size: 0.75rem;
  transition: transform 0.15s;
}
details[open] summary::before { transform: rotate(90deg); }
details[open] summary { margin-bottom: 1rem; }
.tasks { display: flex; flex-direction: column; gap: 0.625rem; }
.task {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 0.875rem 1rem;
  background: #fafafa;
}
.task > header {
  display: flex;
  gap: 0.625rem;
  align-items: center;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}
.task h3 {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 500;
  font-family: ui-monospace, "Cascadia Mono", "Fira Code", Menlo, monospace;
  color: #1f2937;
}
.verdict-badge {
  padding: 0.125rem 0.5rem;
  border-radius: 4px;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: white;
  white-space: nowrap;
}
.verdict-badge.pass { background: #10b981; }
.verdict-badge.fail, .verdict-badge.config_error, .verdict-badge.missing_evaluation { background: #ef4444; }
.verdict-badge.missing { background: #6b7280; }
.verdict-badge.needs_prep { background: #3b82f6; }
.points-pill {
  margin-left: auto;
  padding: 0.125rem 0.5rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  font-family: ui-monospace, "Cascadia Mono", Menlo, monospace;
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #e5e7eb;
}
.points-pill.tier-high { background: #d1fae5; color: #047857; border-color: #a7f3d0; }
.points-pill.tier-mid  { background: #fef3c7; color: #92400e; border-color: #fde68a; }
.points-pill.tier-low  { background: #fee2e2; color: #991b1b; border-color: #fecaca; }
.points-pill.tier-none { background: #f3f4f6; color: #6b7280; border-color: #e5e7eb; }
.task-pipeline {
  font-size: 0.8125rem;
  color: #6b7280;
  margin: 0 0 0.5rem;
}
.task .summary { margin: 0.375rem 0; color: #4b5563; }
.task .failing-gate {
  margin: 0.375rem 0;
  font-size: 0.8125rem;
  color: #991b1b;
  font-family: ui-monospace, "Cascadia Mono", monospace;
}
.section-label {
  margin: 0.75rem 0 0.375rem;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #6b7280;
}
.section-label .total-cost {
  margin-left: 0.375rem;
  color: #991b1b;
  font-family: ui-monospace, "Cascadia Mono", monospace;
}
.diff-list {
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.9375rem;
}
.diff-list li {
  margin-bottom: 0.375rem;
  color: #4b5563;
}
.cost-chip {
  display: inline-block;
  padding: 0.0625rem 0.375rem;
  border-radius: 3px;
  margin-right: 0.375rem;
  font-weight: 600;
  font-size: 0.6875rem;
  letter-spacing: 0.03em;
  font-family: ui-monospace, "Cascadia Mono", monospace;
  background: #fee2e2;
  color: #991b1b;
}
.rule-source {
  display: block;
  margin-top: 0.125rem;
  font-size: 0.75rem;
  color: #6b7280;
  font-family: ui-monospace, "Cascadia Mono", monospace;
}
.task .bonus {
  margin: 0.5rem 0 0;
  padding: 0.5rem 0.75rem;
  background: #eff6ff;
  border-radius: 4px;
  color: #1e3a8a;
  font-size: 0.875rem;
}
.empty-state {
  text-align: center;
  padding: 4rem 2rem;
  color: #6b7280;
}
.empty-state h2 { color: #374151; margin-bottom: 0.5rem; }
.empty-state code {
  background: #f3f4f6;
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-family: ui-monospace, "Cascadia Mono", monospace;
  font-size: 0.875rem;
}
</style>
</head>
<body>
<header class="site-header">
  <h1>SnapLogic Exercise Grades</h1>
  <div class="meta">
    <span id="student-count">0 students</span>
    <span class="dot">·</span>
    <span>Built __BUILT_AT__</span>
  </div>
</header>

<div class="controls">
  <input type="search" id="search" placeholder="Search by student name…">
  <select id="ps-filter"><option value="">All project spaces</option></select>
  <select id="sort">
    <option value="points-desc">Highest total points</option>
    <option value="points-asc">Lowest total points</option>
    <option value="passes-desc">Most passes</option>
    <option value="passes-asc">Fewest passes</option>
    <option value="name-asc">Name (A → Z)</option>
    <option value="date-desc">Most recently graded</option>
  </select>
</div>

<main id="results"></main>

<div id="empty-state" class="empty-state" hidden>
  <h2>No graded students yet</h2>
  <p>Run <code>/grade &lt;student&gt;</code> in Claude Code, then rebuild this page with <code>python -m evaluator.ui</code>.</p>
</div>

<script id="grades-data" type="application/json">__DATA_PLACEHOLDER__</script>
<script>
const DATA = JSON.parse(document.getElementById('grades-data').textContent);
const MAX_POINTS = 10;

function passCount(r) { return (r.counts && r.counts.pass) || 0; }

function pointsEarned(r) {
  if (typeof r.points_earned === 'number') return r.points_earned;
  let total = 0;
  for (const t of (r.tasks || [])) {
    if (typeof t.points === 'number') total += t.points;
  }
  return total;
}

function pointsPossible(r) {
  if (typeof r.points_possible === 'number') return r.points_possible;
  const c = r.counts || {};
  const totalExercises = (c.total != null) ? c.total : (r.tasks || []).length;
  const per = r.max_points_per_exercise || MAX_POINTS;
  return totalExercises * per;
}

function tierForRatio(num, den) {
  if (!den) return 'none';
  const r = num / den;
  if (r >= 0.8) return 'high';
  if (r >= 0.5) return 'mid';
  return 'low';
}

function el(tag, props, ...children) {
  const e = document.createElement(tag);
  if (props) for (const k in props) {
    if (k === 'class') e.className = props[k];
    else if (k === 'text') e.textContent = props[k];
    else e.setAttribute(k, props[k]);
  }
  for (const c of children) if (c != null) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return e;
}

function renderDiff(d) {
  const cost = Number(d.points_deducted || 0);
  const area = d.area || '(unspecified)';
  const desc = d.description || '';
  const reasoning = d.reasoning || '';
  const ruleSrc = d.rule_source || '';

  const li = el('li');
  if (cost > 0) {
    li.appendChild(el('span', {class: 'cost-chip', text: '−' + cost + ' pt' + (cost === 1 ? '' : 's')}));
  }
  const headLine = area + (desc ? ' — ' + desc : '');
  li.appendChild(document.createTextNode(headLine));
  if (reasoning) {
    li.appendChild(document.createTextNode(' — ' + reasoning));
  }
  if (ruleSrc) {
    li.appendChild(el('span', {class: 'rule-source', text: 'rule: ' + ruleSrc}));
  }
  return li;
}

function renderTask(t) {
  const verdict = t.verdict || t.status || 'unknown';
  const verdictLabel = (t.verdict || t.status || 'unknown').replace(/_/g, ' ');
  const div = el('div', {class: 'task'});

  const header = el('header', null,
    el('span', {class: 'verdict-badge ' + verdict, text: verdictLabel}),
    el('h3', {text: t.slug || ''}),
  );

  // Points pill: numeric → X/10 with tier color; null/None → —/10 in muted style.
  const pts = (typeof t.points === 'number') ? t.points : null;
  const tier = (pts === null) ? 'none' : tierForRatio(pts, MAX_POINTS);
  const pointsText = (pts === null ? '—' : pts) + '/' + MAX_POINTS;
  header.appendChild(el('span', {class: 'points-pill tier-' + tier, text: pointsText}));
  div.appendChild(header);

  if (t.student_pipeline_name) {
    div.appendChild(el('p', {class: 'task-pipeline', text: 'Pipeline: ' + t.student_pipeline_name}));
  }
  const body = t.summary || t.reason;
  if (body) div.appendChild(el('p', {class: 'summary', text: body}));

  if (t.failing_gate) {
    const fg = el('p', {class: 'failing-gate'});
    fg.textContent = 'Failing gate: ' + t.failing_gate;
    div.appendChild(fg);
    if (t.failing_gate_detail) {
      const pre = el('pre', {class: 'failing-gate'});
      pre.style.whiteSpace = 'pre-wrap';
      pre.style.background = '#fef2f2';
      pre.style.padding = '0.5rem 0.75rem';
      pre.style.borderRadius = '4px';
      pre.style.fontSize = '0.75rem';
      pre.textContent = t.failing_gate_detail;
      div.appendChild(pre);
    }
  }

  const diffs = t.differences || [];
  const deductions = diffs.filter(d => Number(d.points_deducted || 0) > 0);
  const notes = diffs.filter(d => Number(d.points_deducted || 0) === 0);

  if (deductions.length > 0) {
    const totalCost = deductions.reduce((s, d) => s + Number(d.points_deducted || 0), 0);
    const label = el('div', {class: 'section-label'});
    label.appendChild(document.createTextNode('Deductions'));
    label.appendChild(el('span', {class: 'total-cost', text: '(−' + totalCost + ')'}));
    div.appendChild(label);
    const ul = el('ul', {class: 'diff-list'});
    for (const d of deductions) ul.appendChild(renderDiff(d));
    div.appendChild(ul);
  }

  if (notes.length > 0) {
    div.appendChild(el('div', {class: 'section-label', text: 'Notes (no deduction)'}));
    const ul = el('ul', {class: 'diff-list'});
    for (const d of notes) ul.appendChild(renderDiff(d));
    div.appendChild(ul);
  }

  if (t.bonus_question_answer) {
    div.appendChild(el('p', {class: 'bonus', text: 'Bonus: ' + t.bonus_question_answer}));
  }

  return div;
}

function renderCard(r) {
  const c = r.counts || {};
  const card = el('article', {class: 'student-card'});

  const meta = el('div', {class: 'meta'});
  meta.appendChild(el('span', {class: 'ps', text: r.project_space || '—'}));
  meta.appendChild(document.createTextNode(' · '));
  meta.appendChild(el('span', {text: r.graded_at || ''}));

  card.appendChild(el('header', null,
    el('h2', {text: r.student || '(unknown)'}),
    meta,
  ));

  const earned = pointsEarned(r);
  const possible = pointsPossible(r);
  const totalRow = el('div', {class: 'total-row'});
  const totalTier = tierForRatio(earned, possible);
  const totalBadge = el('span', {class: 'total-badge tier-' + totalTier});
  totalBadge.appendChild(document.createTextNode('Total: ' + earned + '/' + possible + ' pts'));
  if (possible > 0) {
    const pct = Math.round((earned / possible) * 100);
    totalBadge.appendChild(el('span', {class: 'pct', text: '(' + pct + '%)'}));
  }
  totalRow.appendChild(totalBadge);
  card.appendChild(totalRow);

  const badges = el('div', {class: 'badges'});
  badges.appendChild(el('span', {class: 'badge pass', text: (c.pass || 0) + ' pass'}));
  if (c.fail) badges.appendChild(el('span', {class: 'badge fail', text: c.fail + ' fail'}));
  if (c.missing) badges.appendChild(el('span', {class: 'badge missing', text: c.missing + ' missing'}));
  if (c.needs_prep) badges.appendChild(el('span', {class: 'badge needs-prep', text: c.needs_prep + ' needs prep'}));
  card.appendChild(badges);

  if (r.overall_summary) {
    card.appendChild(el('p', {class: 'overall', text: r.overall_summary}));
  }

  const tasks = r.tasks || [];
  if (tasks.length > 0) {
    const details = el('details');
    details.appendChild(el('summary', {text: 'View ' + tasks.length + ' task' + (tasks.length === 1 ? '' : 's')}));
    const tasksDiv = el('div', {class: 'tasks'});
    for (const t of tasks) tasksDiv.appendChild(renderTask(t));
    details.appendChild(tasksDiv);
    card.appendChild(details);
  }
  return card;
}

function init() {
  const resultsEl = document.getElementById('results');
  const emptyEl = document.getElementById('empty-state');
  const searchEl = document.getElementById('search');
  const psEl = document.getElementById('ps-filter');
  const sortEl = document.getElementById('sort');
  const countEl = document.getElementById('student-count');

  if (DATA.length === 0) {
    emptyEl.hidden = false;
    countEl.textContent = '0 students';
    return;
  }

  const spaces = [...new Set(DATA.map(r => r.project_space).filter(Boolean))].sort();
  for (const sp of spaces) {
    const opt = document.createElement('option');
    opt.value = sp; opt.textContent = sp;
    psEl.appendChild(opt);
  }

  function render() {
    const q = searchEl.value.toLowerCase().trim();
    const ps = psEl.value;
    const mode = sortEl.value;

    let items = DATA.slice();
    if (q) items = items.filter(r => (r.student || '').toLowerCase().includes(q));
    if (ps) items = items.filter(r => r.project_space === ps);

    items.sort((a, b) => {
      const nameCmp = (a.student || '').localeCompare(b.student || '');
      if (mode === 'points-desc') {
        const d = pointsEarned(b) - pointsEarned(a);
        return d !== 0 ? d : nameCmp;
      }
      if (mode === 'points-asc') {
        const d = pointsEarned(a) - pointsEarned(b);
        return d !== 0 ? d : nameCmp;
      }
      if (mode === 'passes-desc') {
        const d = passCount(b) - passCount(a);
        return d !== 0 ? d : nameCmp;
      }
      if (mode === 'passes-asc') {
        const d = passCount(a) - passCount(b);
        return d !== 0 ? d : nameCmp;
      }
      if (mode === 'name-asc') return nameCmp;
      if (mode === 'date-desc') return (b.graded_at || '').localeCompare(a.graded_at || '');
      return 0;
    });

    resultsEl.innerHTML = '';
    for (const r of items) resultsEl.appendChild(renderCard(r));

    countEl.textContent = items.length === DATA.length
      ? DATA.length + ' student' + (DATA.length === 1 ? '' : 's')
      : items.length + ' of ' + DATA.length + ' student' + (DATA.length === 1 ? '' : 's');
  }

  searchEl.addEventListener('input', render);
  psEl.addEventListener('change', render);
  sortEl.addEventListener('change', render);
  render();
}

init();
</script>
</body>
</html>
"""


def _collect_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if not GRADES_DIR.exists():
        return reports
    for student_dir in sorted(GRADES_DIR.iterdir()):
        if not student_dir.is_dir():
            continue
        json_path = student_dir / "report.json"
        if not json_path.exists():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"WARNING: {json_path} is not valid JSON ({e}); skipping.", file=sys.stderr)
            continue
        reports.append(data)
    return reports


def cmd_build(open_in_browser: bool) -> int:
    reports = _collect_reports()
    UI_DIR.mkdir(parents=True, exist_ok=True)

    data_json = json.dumps(reports, indent=2, ensure_ascii=False)
    data_json = data_json.replace("</", "<\\/")

    built_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", data_json).replace(
        "__BUILT_AT__", built_at
    )
    UI_INDEX.write_text(html, encoding="utf-8")

    print(f"Wrote {UI_INDEX} ({len(reports)} student report(s) embedded)")
    if reports:
        print("Students included:")
        for r in reports:
            print(f"  - {r.get('student', '?')}")
    else:
        print(
            "No grades/<student>/report.json files found yet. "
            "Run `/grade <student>` first, then re-run this command."
        )

    if open_in_browser:
        print(f"Opening {UI_INDEX} in default browser ...")
        webbrowser.open(UI_INDEX.as_uri())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evaluator.ui",
        description="Generate a single self-contained HTML grade dashboard.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Write ui/index.html but do not open it in the browser.",
    )
    args = parser.parse_args(argv)
    return cmd_build(open_in_browser=not args.no_open)


if __name__ == "__main__":
    raise SystemExit(main())
