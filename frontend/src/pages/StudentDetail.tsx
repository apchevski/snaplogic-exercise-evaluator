import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, pollJob } from "../api";
import { useCanGrade, useIsStudentOnly, useToken } from "../auth";
import { ConfirmModal } from "../components/ConfirmModal";
import {
  IconCheck,
  IconClose,
  IconGrade,
  IconHistory,
} from "../components/icons";
import { StatusPill } from "../components/StatusPill";
import { Panel } from "../components/table";
import { TaskCard, tierForRatio } from "../components/TaskCard";
import {
  TaskEvaluationEditor,
  type TaskEvaluationPayload,
} from "../components/TaskEvaluationEditor";
import type {
  Exercise,
  Job,
  Report,
  ReportEdit,
  ReportEditChange,
  StudentMeta,
  TaskResult,
} from "../types";

// Edit target for the AI-written evaluation: the report's Overall paragraph,
// or one task's whole evaluation (summary + deductions + bonus). Saving
// PATCHes the stored report in place — no re-grade.
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

function formatEditTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** "task:task_03" → "task_03"; "overall" → "Overall summary". */
function editTargetLabel(target: string): string {
  return target === "overall"
    ? "Overall summary"
    : target.startsWith("task:")
      ? target.slice("task:".length)
      : target;
}

function describeChange(c: ReportEditChange): string {
  switch (c.field) {
    case "points":
      return `points ${c.from ?? "—"} → ${c.to ?? "—"}`;
    case "deductions":
      return `deductions ${c.from} → ${c.to}`;
    case "summary":
      return "summary edited";
    case "bonus":
      return "bonus updated";
    case "overall_summary":
      return "overall summary edited";
    default:
      return c.field;
  }
}

export default function StudentDetail() {
  const { slug = "" } = useParams();
  const token = useToken();
  // Students get the same report, minus every action (backend-enforced too).
  const canGrade = useCanGrade();
  // A student has no dashboard to go back to — this is their only page.
  const isStudentOnly = useIsStudentOnly();
  const [student, setStudent] = useState<StudentMeta | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [draft, setDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  // Rendered inside the editor — the page-top error banner can be scrolled
  // out of view when editing a task card further down.
  const [editError, setEditError] = useState<string | null>(null);
  // Confirmation dialog target for a grading run: "all" = Grade all exercises,
  // otherwise a single task (regrade = it already has a result to replace).
  const [gradeConfirm, setGradeConfirm] = useState<
    { kind: "all" } | { kind: "task"; slug: string; regrade: boolean } | null
  >(null);
  // Audit log (admin/mentor only); lazy-loaded when the panel is expanded.
  const [historyOpen, setHistoryOpen] = useState(false);
  const [edits, setEdits] = useState<ReportEdit[] | null>(null);
  const [editsError, setEditsError] = useState<string | null>(null);

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

  useEffect(() => {
    api
      .listExercises(token)
      .then(({ exercises }) => setExercises(exercises))
      .catch(() => setExercises([])); // "not graded" cards degrade gracefully
  }, [token]);

  const loadEdits = useCallback(async () => {
    setEditsError(null);
    try {
      const { edits } = await api.getReportEdits(token, slug);
      setEdits(edits);
    } catch (e) {
      setEditsError(e instanceof Error ? e.message : String(e));
      setEdits([]);
    }
  }, [token, slug]);

  const toggleHistory = () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && edits === null) void loadEdits();
  };

  const name = student?.display_name ?? report?.student ?? slug;

  // The backend holds one grade lock per student, so any in-flight job (full,
  // batch, or single-task) disables every Regrade button.
  const anyBusy = Object.values(jobs).some(
    (j) =>
      j.status === "queued" ||
      j.status === "running" ||
      j.status === "batch_processing",
  );

  const runGrading = useCallback(
    async (jobKey: string, tasks?: string) => {
      setError(null);
      try {
        const { id } = await api.startGrading(token, name, tasks);
        // "Grade all" (no tasks) runs as an async batch — poll longer and stop
        // quietly if it outlasts the page (the report shows on next refresh);
        // a single-task Regrade stays instant on the default poll.
        const fullRun = tasks === undefined;
        const job = await pollJob(
          () => api.getGrading(token, id),
          (j) => setJobs((prev) => ({ ...prev, [jobKey]: j })),
          fullRun
            ? { intervalMs: 8000, timeoutMs: 2 * 60 * 60 * 1000, onTimeout: "stop" }
            : undefined,
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

  const regradeTask = useCallback(
    (taskSlug: string) => runGrading(taskSlug, taskSlug),
    [runGrading],
  );

  const gradeAll = useCallback(() => runGrading("__all__"), [runGrading]);

  const startOverallEdit = (currentText: string) => {
    setEditing({ kind: "overall" });
    setDraft(currentText);
    setEditError(null);
  };

  const startTaskEdit = (taskSlug: string) => {
    setEditing({ kind: "task", slug: taskSlug });
    setEditError(null);
  };

  const cancelEdit = () => {
    setEditing(null);
    setEditError(null);
  };

  // Push a report edit and swap in the returned state. `payload` is either the
  // overall summary or a task's edited fields (summary/differences/bonus).
  const applyEdit = async (
    payload: Parameters<typeof api.updateStudentReport>[2],
  ) => {
    setSavingEdit(true);
    setEditError(null);
    try {
      const updated = await api.updateStudentReport(token, slug, payload);
      setStudent(updated.student);
      setReport(updated.report);
      setEditing(null);
      // Keep the audit panel current if it's already been opened.
      if (edits !== null) void loadEdits();
    } catch (e) {
      setEditError(
        `Saving failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setSavingEdit(false);
    }
  };

  const saveOverall = async () => {
    const text = draft.trim();
    if (!text) return;
    await applyEdit({ overall_summary: text });
  };

  const saveTaskEval = async (payload: TaskEvaluationPayload) => {
    if (editing?.kind !== "task") return;
    await applyEdit({ task: editing.slug, ...payload });
  };

  if (loading) return <main className="page">Loading…</main>;
  if (error && !student && !report) {
    return (
      <main className="page">
        <div className="error-banner">{error}</div>
        {!isStudentOnly && (
          <Link className="back-link" to="/">
            ← Back to dashboard
          </Link>
        )}
      </main>
    );
  }

  const counts = report?.counts ?? student?.counts;
  const earned = report?.points_earned ?? student?.points_earned ?? 0;
  const possible = report?.points_possible ?? student?.points_possible ?? 0;
  const tier = tierForRatio(earned, possible);
  const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;

  // Unsynced exercises are surfaced as a warning instead of task cards —
  // they were skipped by grading, so there is no verdict to show.
  const unsynced =
    report?.tasks.filter((t) => t.status === "needs_sync" || t.status === "needs_prep") ?? [];
  const needsSyncCount = unsynced.length || (counts?.needs_sync ?? counts?.needs_prep ?? 0);
  const gradedTasks =
    report?.tasks.filter((t) => t.status !== "needs_sync" && t.status !== "needs_prep") ?? [];

  // Registered exercises with no verdict at all for this student — never
  // graded, or added to the exercise set after the last grading run.
  const reportSlugs = new Set((report?.tasks ?? []).map((t) => t.slug));
  const notGradedExercises = exercises.filter(
    (e) => !e.archived && !e.missing_from_image && !reportSlugs.has(e.slug),
  );

  const overallText = report?.overall_summary ?? student?.overall_summary ?? "";

  // The report is authoritative once graded; before the first grade we fall
  // back to the meta so the path still shows (with no date — nothing graded).
  const projectPath =
    report?.student_project_path ?? student?.student_project_path;
  const gradedAt = report?.graded_at ?? student?.graded_at;

  // Overall-summary editor: a single textarea. Task evaluations use the richer
  // TaskEvaluationEditor (summary + deductions + bonus) rendered per-card.
  const overallEditorNode = (
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
          onClick={() => void saveOverall()}
          disabled={savingEdit || !draft.trim()}
        >
          <IconCheck />
          {savingEdit ? "Saving…" : "Save"}
        </button>
        <button className="btn small" onClick={cancelEdit} disabled={savingEdit}>
          <IconClose />
          Cancel
        </button>
      </div>
      {editError && <div className="job-error">{editError}</div>}
    </div>
  );

  const editPencil = (title: string, onClick: () => void) =>
    canGrade ? (
      <button
        className="btn small icon-btn"
        title={title}
        aria-label={title}
        onClick={onClick}
        disabled={anyBusy || savingEdit}
      >
        <PencilIcon />
      </button>
    ) : null;

  const taskEditor = (t: TaskResult) =>
    editing?.kind === "task" && editing.slug === t.slug ? (
      <TaskEvaluationEditor
        task={t}
        saving={savingEdit}
        error={editError}
        onSave={(payload) => void saveTaskEval(payload)}
        onCancel={cancelEdit}
      />
    ) : undefined;

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      {canGrade && needsSyncCount > 0 && (
        <div className="warn-banner">
          ⚠ {needsSyncCount} exercise{needsSyncCount === 1 ? " was" : "s were"} skipped
          because {needsSyncCount === 1 ? "its" : "their"} grading artifacts are not
          synced{unsynced.length > 0 && <>: {unsynced.map((t) => t.slug).join(", ")}</>}.
          Sync {needsSyncCount === 1 ? "it" : "them"} on the Exercises page, then regrade.
        </div>
      )}
      {!isStudentOnly && (
        <Link className="back-link" to="/">
          ← Back to dashboard
        </Link>
      )}
      <Panel
        title={`Grade Summary — ${name}`}
        hint="Total points, per-verdict counts, and the overall summary from the latest grading run."
      >
        <div className="panel-body">
          <div className="detail-meta">
            {projectPath && <span className="ps">{projectPath}</span>}
            {/* Never graded: show the project path alone, no "graded" date. */}
            {gradedAt && (
              <>
                {projectPath && " · "}
                <span>graded {gradedAt}</span>
              </>
            )}
          </div>
          <span className={`total-badge tier-${tier}`}>
            Total: {earned}/{possible} pts
            {pct !== null && <span className="pct">({pct}%)</span>}
          </span>
          {(counts || notGradedExercises.length > 0) && (
            <div className="badges">
              {counts && (
                <>
                  <span className="badge pass">{counts.pass} pass</span>
                  <span className="badge fail">{counts.fail} fail</span>
                  <span className="badge missing">{counts.missing} missing</span>
                </>
              )}
              {notGradedExercises.length > 0 && (
                <span className="badge notgraded">
                  {notGradedExercises.length} not graded
                </span>
              )}
            </div>
          )}
          {editing?.kind === "overall" ? (
            overallEditorNode
          ) : report ? (
            <div className="overall-row">
              {overallText ? (
                <p className="overall">{overallText}</p>
              ) : (
                <p className="overall muted">No overall summary yet.</p>
              )}
              {editPencil("Edit overall summary", () =>
                startOverallEdit(overallText),
              )}
            </div>
          ) : (
            overallText && <p className="overall">{overallText}</p>
          )}
        </div>
      </Panel>
      <Panel
        title="Task Results"
        hint="Verdict, points, and deductions for every registered exercise. Exercises never graded for this student show as “not graded” — grade them one at a time, or regrade any graded one."
      >
        <div className="panel-body">
          {!report && (
            <div className="grade-all-row">
              <p className="summary" style={{ margin: 0 }}>
                Nothing graded yet for this student.
              </p>
              {canGrade && (
                <>
                  <button
                    className="btn small primary"
                    onClick={() => setGradeConfirm({ kind: "all" })}
                    disabled={anyBusy}
                  >
                    <IconGrade />
                    Grade all exercises
                  </button>
                  {jobs["__all__"] && <StatusPill job={jobs["__all__"]} kind="grade" />}
                </>
              )}
            </div>
          )}
          {gradedTasks.length > 0 || notGradedExercises.length > 0 ? (
            <div className="tasks">
              {gradedTasks.map((t) => (
                <TaskCard
                  key={t.slug}
                  task={t}
                  editor={taskEditor(t)}
                  action={
                    canGrade ? (
                      <span className="actions-cell">
                        {jobs[t.slug] && <StatusPill job={jobs[t.slug]} kind="grade" />}
                        {editPencil("Edit evaluation", () => startTaskEdit(t.slug))}
                        <button
                          className="btn small"
                          onClick={() =>
                            setGradeConfirm({ kind: "task", slug: t.slug, regrade: true })
                          }
                          disabled={anyBusy}
                        >
                          <IconGrade />
                          Regrade
                        </button>
                      </span>
                    ) : undefined
                  }
                />
              ))}
              {notGradedExercises.map((e) => (
                <div className="task v-notgraded" key={e.slug}>
                  <header>
                    <span className="verdict-badge notgraded">not graded</span>
                    <h3>{e.slug}</h3>
                    <span className="points-pill tier-none">—/10</span>
                    {canGrade && (
                      <span className="actions-cell">
                        {jobs[e.slug] && <StatusPill job={jobs[e.slug]} kind="grade" />}
                        {e.sync_status !== "ready" && (
                          <span
                            className="warn-chip"
                            title="Not synced — grading will skip it until it's synced on the Exercises page."
                          >
                            ⚠
                          </span>
                        )}
                        <button
                          className="btn small"
                          onClick={() =>
                            setGradeConfirm({ kind: "task", slug: e.slug, regrade: false })
                          }
                          disabled={anyBusy}
                        >
                          <IconGrade />
                          Grade
                        </button>
                      </span>
                    )}
                  </header>
                  <p className="summary muted">
                    This exercise has never been graded for this student.
                  </p>
                </div>
              ))}
            </div>
          ) : (
            !report && <p className="summary">No exercises registered yet.</p>
          )}
        </div>
      </Panel>
      {canGrade && report && (
        <Panel
          title="Edit history"
          hint="Every manual change to this report — who edited what, and when. AI-only evaluations never appear here."
        >
          <div className="panel-body">
            <button className="btn small" onClick={toggleHistory}>
              <IconHistory />
              {historyOpen ? "Hide edit history" : "Show edit history"}
            </button>
            {historyOpen && (
              <div className="edit-history">
                {editsError && <div className="job-error">{editsError}</div>}
                {edits === null && !editsError && (
                  <p className="summary muted">Loading…</p>
                )}
                {edits !== null && edits.length === 0 && !editsError && (
                  <p className="summary muted">
                    No manual edits — this report is exactly as graded.
                  </p>
                )}
                {edits !== null && edits.length > 0 && (
                  <ul className="edit-list">
                    {edits.map((e, i) => (
                      <li key={i} className="edit-entry">
                        <div className="edit-entry-head">
                          <span className="edit-target">{editTargetLabel(e.target)}</span>
                          <span className="edit-when">{formatEditTime(e.edited_at)}</span>
                        </div>
                        <div className="edit-by">by {e.edited_by}</div>
                        <ul className="edit-changes">
                          {e.changes.map((c, j) => (
                            <li key={j}>{describeChange(c)}</li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </Panel>
      )}
      {gradeConfirm && canGrade && (
        <ConfirmModal
          title={
            gradeConfirm.kind === "all"
              ? "Grade All Exercises"
              : gradeConfirm.regrade
                ? "Regrade Exercise"
                : "Grade Exercise"
          }
          confirmLabel={
            gradeConfirm.kind === "all"
              ? "Grade all"
              : gradeConfirm.regrade
                ? "Regrade"
                : "Grade"
          }
          confirmIcon={<IconGrade />}
          confirmClassName="btn primary"
          busyLabel="Starting…"
          onConfirm={async () => {
            const target = gradeConfirm;
            setGradeConfirm(null);
            if (!target) return;
            if (target.kind === "all") void gradeAll();
            else void regradeTask(target.slug);
          }}
          onClose={() => setGradeConfirm(null)}
        >
          {gradeConfirm.kind === "all" ? (
            <p>
              Grade <strong>all exercises</strong> for {name}? This runs the AI
              grader over every registered exercise and refreshes the overall
              summary. It runs in the background.
            </p>
          ) : (
            <p>
              {gradeConfirm.regrade ? "Regrade" : "Grade"}{" "}
              <strong>{gradeConfirm.slug}</strong> for {name}? This runs the AI
              grader{" "}
              {gradeConfirm.regrade
                ? "and replaces this exercise’s current result"
                : "and records a result for this exercise"}
              . It runs in the background.
            </p>
          )}
        </ConfirmModal>
      )}
    </main>
  );
}
