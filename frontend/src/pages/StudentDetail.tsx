import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, pollJob } from "../api";
import { useToken } from "../auth";
import { StatusPill } from "../components/StatusPill";
import { Panel } from "../components/table";
import { TaskCard, tierForRatio } from "../components/TaskCard";
import type { Job, Report, StudentMeta } from "../types";

export default function StudentDetail() {
  const { slug = "" } = useParams();
  const token = useToken();
  const [student, setStudent] = useState<StudentMeta | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { student, report } = await api.getStudent(token, slug);
      setStudent(student);
      setReport(report);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token, slug]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const name = student?.display_name ?? report?.student ?? slug;

  // The backend holds one grade lock per student, so any queued/running
  // job (full or single-task) disables every Regrade button.
  const anyBusy = Object.values(jobs).some(
    (j) => j.status === "queued" || j.status === "running",
  );

  const regradeTask = useCallback(
    async (taskSlug: string) => {
      setError(null);
      try {
        const { id } = await api.startGrading(token, name, taskSlug);
        const job = await pollJob(
          () => api.getGrading(token, id),
          (j) => setJobs((prev) => ({ ...prev, [taskSlug]: j })),
        );
        if (job.status === "succeeded") void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, name, refresh],
  );

  if (loading) return <main className="page">Loading…</main>;
  if (error && !student && !report) {
    return (
      <main className="page">
        <div className="error-banner">{error}</div>
        <Link className="back-link" to="/">
          ← Back to dashboard
        </Link>
      </main>
    );
  }

  const counts = report?.counts ?? student?.counts;
  const earned = report?.points_earned ?? student?.points_earned ?? 0;
  const possible = report?.points_possible ?? student?.points_possible ?? 0;
  const tier = tierForRatio(earned, possible);
  const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      <Link className="back-link" to="/">
        ← Back to dashboard
      </Link>
      <Panel
        title={`Grade Summary — ${name}`}
        hint="Total points, per-verdict counts, and the overall summary from the latest grading run."
      >
        <div className="panel-body">
          <div className="detail-meta">
            {report?.student_project_path && (
              <>
                <span className="ps">{report.student_project_path}</span>
                {" · "}
              </>
            )}
            <span>
              graded {report?.graded_at ?? student?.graded_at ?? "never"}
            </span>
          </div>
          <span className={`total-badge tier-${tier}`}>
            Total: {earned}/{possible} pts
            {pct !== null && <span className="pct">({pct}%)</span>}
          </span>
          {counts && (
            <div className="badges">
              <span className="badge pass">{counts.pass} pass</span>
              <span className="badge fail">{counts.fail} fail</span>
              <span className="badge missing">{counts.missing} missing</span>
              <span className="badge needs-prep">{counts.needs_prep} needs prep</span>
            </div>
          )}
          {(report?.overall_summary ?? student?.overall_summary) && (
            <p className="overall">
              {report?.overall_summary ?? student?.overall_summary}
            </p>
          )}
        </div>
      </Panel>
      <Panel
        title="Task Results"
        hint="Verdict, points, and deductions for every registered exercise. Regrade re-runs just that exercise — cheaper and faster than a full grading."
      >
        <div className="panel-body">
          {report ? (
            <div className="tasks">
              {report.tasks.map((t) => (
                <TaskCard
                  key={t.slug}
                  task={t}
                  action={
                    <span className="actions-cell">
                      {jobs[t.slug] && <StatusPill job={jobs[t.slug]} kind="grade" />}
                      <button
                        className="btn small"
                        onClick={() => void regradeTask(t.slug)}
                        disabled={anyBusy}
                      >
                        Regrade
                      </button>
                    </span>
                  }
                />
              ))}
            </div>
          ) : (
            <p className="summary">No stored report yet — run a grading first.</p>
          )}
        </div>
      </Panel>
    </main>
  );
}
