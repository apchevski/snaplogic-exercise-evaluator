import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, pollJob } from "../api";
import { useToken } from "../auth";
import { StatusPill } from "../components/StatusPill";
import { Panel } from "../components/table";
import { TaskCard, tierForRatio } from "../components/TaskCard";
import type { Job, Report, StudentMeta, TaskResult } from "../types";

// Edit target for the AI-written text: the report's Overall paragraph or one
// task's summary. Saving PATCHes the stored report in place — no re-grade.
type EditTarget = { kind: "overall" } | { kind: "task"; slug: string };

function PencilIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
    </svg>
  );
}

export default function StudentDetail() {
  const { slug = "" } = useParams();
  const token = useToken();
  const [student, setStudent] = useState<StudentMeta | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [draft, setDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

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
        if (job.status === "succeeded") {
          // Drop any in-progress text edit: the regraded report replaces it.
          setEditing(null);
          void refresh();
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, name, refresh],
  );

  const startEdit = (target: EditTarget, currentText: string) => {
    setEditing(target);
    setDraft(currentText);
  };

  const saveEdit = async () => {
    if (!editing) return;
    const text = draft.trim();
    if (!text) return;
    setSavingEdit(true);
    setError(null);
    try {
      const payload =
        editing.kind === "overall"
          ? { overall_summary: text }
          : { task: editing.slug, summary: text };
      const updated = await api.updateStudentReport(token, slug, payload);
      setStudent(updated.student);
      setReport(updated.report);
      setEditing(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingEdit(false);
    }
  };

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

  // Unprepped exercises are surfaced as a warning instead of task cards —
  // they were skipped by grading, so there is no verdict to show.
  const unprepped = report?.tasks.filter((t) => t.status === "needs_prep") ?? [];
  const needsPrepCount = unprepped.length || (counts?.needs_prep ?? 0);
  const gradedTasks = report?.tasks.filter((t) => t.status !== "needs_prep") ?? [];

  const overallText = report?.overall_summary ?? student?.overall_summary ?? "";

  // Shared inline editor rendered in place of whichever text is being edited.
  const editorNode = (
    <div className="summary-editor">
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        disabled={savingEdit}
        autoFocus
      />
      <div className="editor-actions">
        <button
          className="btn small primary"
          onClick={() => void saveEdit()}
          disabled={savingEdit || !draft.trim()}
        >
          {savingEdit ? "Saving…" : "Save"}
        </button>
        <button
          className="btn small"
          onClick={() => setEditing(null)}
          disabled={savingEdit}
        >
          Cancel
        </button>
      </div>
    </div>
  );

  const editPencil = (title: string, onClick: () => void) => (
    <button
      className="btn small icon-btn"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={anyBusy || savingEdit}
    >
      <PencilIcon />
    </button>
  );

  const taskEditor = (t: TaskResult) =>
    editing?.kind === "task" && editing.slug === t.slug ? editorNode : undefined;

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      {needsPrepCount > 0 && (
        <div className="warn-banner">
          ⚠ {needsPrepCount} exercise{needsPrepCount === 1 ? " was" : "s were"} skipped
          because {needsPrepCount === 1 ? "its" : "their"} grading artifacts are not
          prepped{unprepped.length > 0 && <>: {unprepped.map((t) => t.slug).join(", ")}</>}.
          Prep {needsPrepCount === 1 ? "it" : "them"} on the Exercises page, then regrade.
        </div>
      )}
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
            </div>
          )}
          {editing?.kind === "overall" ? (
            editorNode
          ) : report ? (
            <div className="overall-row">
              {overallText ? (
                <p className="overall">{overallText}</p>
              ) : (
                <p className="overall muted">No overall summary yet.</p>
              )}
              {editPencil("Edit overall summary", () =>
                startEdit({ kind: "overall" }, overallText),
              )}
            </div>
          ) : (
            overallText && <p className="overall">{overallText}</p>
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
              {gradedTasks.map((t) => (
                <TaskCard
                  key={t.slug}
                  task={t}
                  summaryEditor={taskEditor(t)}
                  action={
                    <span className="actions-cell">
                      {jobs[t.slug] && <StatusPill job={jobs[t.slug]} kind="grade" />}
                      {editPencil("Edit summary", () =>
                        startEdit(
                          { kind: "task", slug: t.slug },
                          t.summary || t.reason || "",
                        ),
                      )}
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
