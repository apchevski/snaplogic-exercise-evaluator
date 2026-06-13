import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { useToken } from "../auth";
import { TaskCard, tierForRatio } from "../components/TaskCard";
import type { Report, StudentMeta } from "../types";

export default function StudentDetail() {
  const { slug = "" } = useParams();
  const token = useToken();
  const [student, setStudent] = useState<StudentMeta | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getStudent(token, slug)
      .then(({ student, report }) => {
        setStudent(student);
        setReport(report);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [token, slug]);

  if (loading) return <main>Loading…</main>;
  if (error) {
    return (
      <main>
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
    <main>
      <Link className="back-link" to="/">
        ← Back to dashboard
      </Link>
      <article className="student-card" style={{ marginTop: "1rem" }}>
        <header>
          <h2>{student?.display_name ?? report?.student ?? slug}</h2>
          <div className="meta">
            {report?.student_project_path && (
              <span className="ps">{report.student_project_path}</span>
            )}
            {" · "}
            <span>{report?.graded_at ?? student?.graded_at ?? ""}</span>
          </div>
        </header>
        <div className="total-row">
          <span className={`total-badge tier-${tier}`}>
            Total: {earned}/{possible} pts
            {pct !== null && <span className="pct">({pct}%)</span>}
          </span>
        </div>
        {counts && (
          <div className="badges">
            <span className="badge pass">{counts.pass} pass</span>
            <span className="badge fail">{counts.fail} fail</span>
            <span className="badge missing">{counts.missing} missing</span>
            <span className="badge needs-prep">{counts.needs_prep} needs prep</span>
          </div>
        )}
        {(report?.overall_summary ?? student?.overall_summary) && (
          <p className="overall">{report?.overall_summary ?? student?.overall_summary}</p>
        )}
        {report ? (
          <div className="tasks">
            {report.tasks.map((t) => (
              <TaskCard key={t.slug} task={t} />
            ))}
          </div>
        ) : (
          <p className="summary">No stored report yet — run a grading first.</p>
        )}
      </article>
    </main>
  );
}
