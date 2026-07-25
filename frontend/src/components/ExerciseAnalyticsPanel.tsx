import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { useToken } from "../auth";
import type { Exercise, ExerciseAnalytics } from "../types";
import { IconChart } from "./icons";
import { Panel } from "./table";

/** Stacked pass/fail/missing bar for one exercise. */
function VerdictBar({ row }: { row: ExerciseAnalytics }) {
  const total = row.pass + row.fail + row.missing;
  if (total === 0) return <span className="cell-muted">—</span>;
  const pct = (n: number) => `${(n / total) * 100}%`;
  return (
    <span
      className="analytics-bar"
      title={`${row.pass} pass · ${row.fail} fail · ${row.missing} missing`}
    >
      {row.pass > 0 && <span className="seg-pass" style={{ width: pct(row.pass) }} />}
      {row.fail > 0 && <span className="seg-fail" style={{ width: pct(row.fail) }} />}
      {row.missing > 0 && <span className="seg-missing" style={{ width: pct(row.missing) }} />}
    </span>
  );
}

/** Per-exercise aggregates across every student's current report: verdict
 * split, average points, and the deduction rules that cost the cohort points
 * most often. Helps an instructor see which exercises or rules need tuning.
 * Collapsible and lazy-loaded — it reads every student's report server-side,
 * so it only fetches when opened. Admin/mentor only (the endpoint 403s
 * students). `exercises` supplies human-readable titles for the slugs. */
export function ExerciseAnalyticsPanel({ exercises }: { exercises: Exercise[] }) {
  const token = useToken();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<ExerciseAnalytics[] | null>(null);
  const [reported, setReported] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const titleFor = useCallback(
    (slug: string) => exercises.find((e) => e.slug === slug)?.title ?? slug,
    [exercises],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const { exercises: data, students_reported } = await api.getExerciseAnalytics(token);
      setRows(data);
      setReported(students_reported);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRows([]);
    }
  }, [token]);

  useEffect(() => {
    if (open && rows === null) void load();
  }, [open, rows, load]);

  return (
    <Panel
      title="Exercise Analytics"
      hint="How the whole cohort did on each exercise — the pass/fail/missing split, the average score, and the deduction rules that cost points most often. Reads every student's current report, so it loads on demand."
    >
      <div className="panel-body">
        <button className="btn small" onClick={() => setOpen((o) => !o)}>
          <IconChart />
          {open ? "Hide analytics" : "Show analytics"}
        </button>
        {open && (
          <div className="analytics">
            {error && <div className="job-error">{error}</div>}
            {rows === null && !error && <p className="summary muted">Loading…</p>}
            {rows !== null && rows.length === 0 && !error && (
              <p className="summary muted">No graded reports to analyze yet.</p>
            )}
            {rows !== null && rows.length > 0 && (
              <>
                <p className="summary muted">
                  Across {reported} graded student{reported === 1 ? "" : "s"}.
                </p>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th className="plain">Exercise</th>
                        <th className="plain">Verdicts</th>
                        <th className="plain">Avg points</th>
                        <th className="plain">Most common deductions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.slug}>
                          <td title={r.slug}>{titleFor(r.slug)}</td>
                          <td>
                            <VerdictBar row={r} />{" "}
                            <span className="cell-muted">
                              {r.pass}/{r.fail}/{r.missing}
                            </span>
                          </td>
                          <td>{r.avg_points ?? "—"}</td>
                          <td>
                            {r.top_deductions.length === 0 ? (
                              <span className="cell-muted">none</span>
                            ) : (
                              r.top_deductions.map((d) => (
                                <span className="deduction-chip" key={d.rule}>
                                  {d.rule} ×{d.count}
                                </span>
                              ))
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}
