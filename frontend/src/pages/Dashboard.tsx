import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "react-oidc-context";
import { Link } from "react-router-dom";

import {
  elapsedSince,
  useActiveGradings,
  useOnGradingFinished,
} from "../activeJobs";
import { api, pollJob } from "../api";
import { useCanGrade, useIsAdmin, useToken } from "../auth";
import { ConfirmModal } from "../components/ConfirmModal";
import { GradeScopeModal } from "../components/GradeScopeModal";
import {
  IconDownload,
  IconEdit,
  IconGrade,
  IconPlus,
  IconTrash,
} from "../components/icons";
import { StatusPill } from "../components/StatusPill";
import { StudentModal } from "../components/StudentModal";
import {
  PagerFooter,
  Panel,
  RowCheckbox,
  SearchBox,
  SortableTh,
  nextSort,
  usePagination,
  type SortState,
} from "../components/table";
import { tierForRatio } from "../components/TaskCard";
import type { Exercise, Job, StudentMeta } from "../types";

// Exercises with any verdict at all. "Not graded" is the registered-exercise
// count minus this, and that count is the same for every row, so sorting by
// gradedTotal ascending is sorting by "not graded" descending.
const gradedTotal = (s: StudentMeta) => {
  const c = s.counts;
  return (c?.pass ?? 0) + (c?.fail ?? 0) + (c?.missing ?? 0) + (c?.needs_sync ?? c?.needs_prep ?? 0);
};

const COMPARE: Record<string, (a: StudentMeta, b: StudentMeta) => number> = {
  student: (a, b) => a.display_name.localeCompare(b.display_name),
  space: (a, b) => (a.space ?? "").localeCompare(b.space ?? ""),
  // Project defaults to the student name when unset, so sort by the effective value.
  project: (a, b) =>
    (a.project ?? a.display_name).localeCompare(b.project ?? b.display_name),
  points: (a, b) => (a.points_earned ?? 0) - (b.points_earned ?? 0),
  pass: (a, b) => (a.counts?.pass ?? 0) - (b.counts?.pass ?? 0),
  fail: (a, b) => (a.counts?.fail ?? 0) - (b.counts?.fail ?? 0),
  missing: (a, b) => (a.counts?.missing ?? 0) - (b.counts?.missing ?? 0),
  notgraded: (a, b) => gradedTotal(b) - gradedTotal(a),
  graded: (a, b) => (a.graded_at ?? "").localeCompare(b.graded_at ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  student: "asc",
  space: "asc",
  project: "asc",
  points: "desc",
  pass: "desc",
  fail: "desc",
  missing: "desc",
  notgraded: "desc",
  graded: "desc",
};

function Count({ n, kind }: { n: number; kind: string }) {
  return <span className={n > 0 ? `count-${kind}` : "count-zero"}>{n}</span>;
}

/** Quote a CSV field only when it contains a comma, quote, or newline. */
function csvField(value: string | number | null | undefined): string {
  const s = value == null ? "" : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build a roster CSV from the students currently shown (respects the active
 * search + sort) and trigger a browser download. Rank mirrors the table's
 * position column. */
function downloadRosterCsv(rows: StudentMeta[], activeCount: number): void {
  const header = [
    "rank",
    "student",
    "project_space",
    "project",
    "total_points",
    "points_possible",
    "pass",
    "fail",
    "missing",
    "not_graded",
    "last_graded",
  ];
  const lines = [header.join(",")];
  rows.forEach((s, i) => {
    const c = s.counts;
    const graded =
      (c?.pass ?? 0) + (c?.fail ?? 0) + (c?.missing ?? 0) + (c?.needs_sync ?? c?.needs_prep ?? 0);
    const notGraded = activeCount > 0 ? Math.max(0, activeCount - graded) : 0;
    lines.push(
      [
        i + 1,
        csvField(s.display_name),
        csvField(s.space ?? ""),
        csvField(s.project ?? s.display_name),
        s.points_earned ?? 0,
        s.points_possible ?? 0,
        c?.pass ?? 0,
        c?.fail ?? 0,
        c?.missing ?? 0,
        notGraded,
        csvField(s.graded_at ?? ""),
      ].join(","),
    );
  });
  const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().slice(0, 10);
  a.download = `roster-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Row-position badge: gold/silver/bronze medals for the top three rows. */
function RankBadge({ rank }: { rank: number }) {
  const medal = rank <= 3 ? ` medal-${rank}` : "";
  return (
    <span className={`rank-badge${medal}`} aria-label={`Rank ${rank}`}>
      {rank}
    </span>
  );
}

export default function Dashboard() {
  const auth = useAuth();
  const token = useToken();
  const isAdmin = useIsAdmin();
  // Students see the same table, minus every action (backend-enforced too).
  const canGrade = useCanGrade();
  // Students may only open their OWN detailed evaluation; every other name
  // renders as plain text. Their own row is the one whose email matches the
  // login (the backend strips the email from everyone else's rows).
  const myEmail = (auth.user?.profile?.email ?? "").trim().toLowerCase();
  const canOpen = (s: StudentMeta) =>
    canGrade || (s.email ?? "").trim().toLowerCase() === myEmail;
  const [students, setStudents] = useState<StudentMeta[]>([]);
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "points", dir: "desc" });
  const [perPage, setPerPage] = useState(25);
  const [adding, setAdding] = useState(false);
  // The student the admin-only Edit dialog is open on.
  const [editing, setEditing] = useState<StudentMeta | null>(null);
  const [defaultSpace, setDefaultSpace] = useState("");
  // Grade-scope picker: which student a grading is being configured for.
  const [scopeFor, setScopeFor] = useState<{ name: string; slug: string } | null>(null);
  // Confirmation dialog targets for the admin-only permanent Remove.
  const [removing, setRemoving] = useState<StudentMeta[] | null>(null);
  // Confirmation dialog for a bulk "grade all exercises" run across many
  // students (each queues its own full-run job; the worker runs them serially).
  const [bulkGrading, setBulkGrading] = useState<StudentMeta[] | null>(null);
  // Row selection (graders): the toolbar's Grade/Remove buttons act on these
  // students. Stored as slugs so a refresh keeps the selection.
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { students } = await api.listStudents(token);
      setStudents(students);
      setError(null);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Gradings in flight anywhere in the deployment — not just ones this tab
  // started. Survives a browser refresh and covers other people's runs, which
  // is what makes the "already grading" guard below trustworthy.
  const {
    jobs: activeGradeJobs,
    forStudent: activeGradingFor,
    refresh: refreshActiveGradings,
  } = useActiveGradings(token, canGrade);
  // Someone's run just ended (maybe in another session) → pull fresh grades.
  useOnGradingFinished(activeGradeJobs, refresh);

  useEffect(() => {
    api
      .listExercises(token)
      .then(({ exercises }) => setExercises(exercises))
      .catch(() => setExercises([])); // chip/scope picker degrade gracefully
  }, [token]);

  // Default student project space, prefilled in the Add Student dialog.
  // Students can't open that dialog (and /v1/config 403s them) — skip.
  useEffect(() => {
    if (!canGrade) return;
    api
      .getConfig(token)
      .then(({ config }) => setDefaultSpace(config.student_project_space ?? ""))
      .catch(() => setDefaultSpace("")); // dialog still works, field just empty
  }, [token, canGrade]);

  const activeExercises = useMemo(
    () => exercises.filter((e) => !e.archived && !e.missing_from_image),
    [exercises],
  );

  const startGrade = useCallback(
    async (studentName: string, slugHint?: string, tasks?: string[] | null) => {
      const key = slugHint ?? studentName;
      setError(null);
      try {
        const { id } = await api.startGrading(token, studentName, tasks ?? undefined, {
          slug: slugHint,
        });
        // Show it in the "Gradings in Progress" panel immediately instead of
        // waiting out the poll interval.
        refreshActiveGradings();
        // A full "grade all" run (tasks == null) judges via the async Batch
        // API — it can take minutes to ~1h, so poll longer and, if it outlasts
        // the browser, stop quietly (the report lands on the next refresh).
        const fullRun = tasks == null;
        const job = await pollJob(
          () => api.getGrading(token, id),
          (j) => setJobs((prev) => ({ ...prev, [key]: j })),
          fullRun
            ? { intervalMs: 8000, timeoutMs: 2 * 60 * 60 * 1000, onTimeout: "stop" }
            : undefined,
        );
        // Terminal: drop it out of the in-flight list now rather than leaving
        // a stale "Grading…" up for the rest of the poll interval.
        refreshActiveGradings();
        if (job.status === "succeeded") void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, refresh, refreshActiveGradings],
  );

  // Bulk grade: queue a full "grade all exercises" run for each selected
  // student. Fired concurrently, but the worker's concurrency-1 lock runs
  // them one at a time; each row's StatusPill tracks its own job. Clears the
  // selection as each job is accepted so the toolbar reflects progress.
  const bulkGrade = useCallback(
    async (targets: StudentMeta[]) => {
      setBulkGrading(null);
      await Promise.all(
        targets.map((s) => {
          setSelectedSlugs((prev) => {
            const next = new Set(prev);
            next.delete(s.slug);
            return next;
          });
          return startGrade(s.display_name, s.slug, null);
        }),
      );
    },
    [startGrade],
  );

  // Adding a student never grades anything — the backend first checks the
  // matching SnapLogic project exists, then creates the card ($0 spent).
  // Errors propagate to the dialog, which stays open and shows them.
  const registerOnly = useCallback(
    async (studentName: string, space?: string, project?: string, email?: string) => {
      await api.registerStudent(token, studentName, space, project, email);
      void refresh();
    },
    [token, refresh],
  );

  // Save the Edit dialog (admin only). Every field is sent so clearing one
  // clears it server-side: null resets the project to the display-name
  // default and drops the student's login. The slug stays as registered, so
  // a renamed student keeps their grades and history. Errors propagate to the
  // dialog, which stays open and shows them.
  const saveStudent = useCallback(
    async (
      target: StudentMeta,
      studentName: string,
      space?: string,
      project?: string,
      email?: string,
    ) => {
      await api.updateStudent(token, target.slug, {
        student: studentName,
        space,
        project: project ?? null,
        email: email ?? null,
      });
      void refresh();
    },
    [token, refresh],
  );

  // Permanent removal (admin only): the API purges each card, report history,
  // job rows, and every stored report file. Runs sequentially so a failure
  // names the student it hit; already-removed students stay removed. Errors
  // propagate to the confirmation dialog, which stays open and shows them.
  const removeStudents = useCallback(
    async (targets: StudentMeta[]) => {
      const failures: string[] = [];
      for (const s of targets) {
        try {
          await api.deleteStudent(token, s.slug);
          setSelectedSlugs((prev) => {
            const next = new Set(prev);
            next.delete(s.slug);
            return next;
          });
        } catch (e) {
          failures.push(
            `${s.display_name}: ${e instanceof Error ? e.message : String(e)}`,
          );
        }
      }
      void refresh();
      if (failures.length > 0) throw new Error(failures.join(" — "));
      setRemoving(null);
    },
    [token, refresh],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = students.filter(
      (s) =>
        !q ||
        s.display_name.toLowerCase().includes(q) ||
        (s.space ?? "").toLowerCase().includes(q) ||
        (s.project ?? "").toLowerCase().includes(q),
    );
    const cmp = COMPARE[sort.key] ?? COMPARE.points;
    const sign = sort.dir === "asc" ? 1 : -1;
    // Ties always break A→Z by name regardless of sort direction, so equal
    // totals rank alphabetically.
    return [...filtered].sort(
      (a, b) => cmp(a, b) * sign || a.display_name.localeCompare(b.display_name),
    );
  }, [students, search, sort]);

  const { page, setPage, pageItems, pageCount } = usePagination(visible, perPage);

  const jobBusy = (key: string) => {
    const j = jobs[key];
    return (
      !!j &&
      (j.status === "queued" ||
        j.status === "running" ||
        j.status === "batch_processing")
    );
  };

  // True while ANY grading for this student is in flight — one this tab
  // started, or one an admin/mentor started elsewhere. The backend holds a
  // single grade lock per student, so a second run would 409 anyway.
  const studentBusy = (s: StudentMeta) =>
    jobBusy(s.slug) || jobBusy(s.display_name) || activeGradingFor(s.slug) !== undefined;

  const onSort = (key: string) => setSort((s) => nextSort(s, key, DEFAULT_DIR[key] ?? "asc"));
  const sc = (key: string) => (sort.key === key ? "sorted" : "");

  const jobEntries = Object.entries(jobs);
  const nameFor = (key: string) =>
    students.find((s) => s.slug === key || s.display_name === key)?.display_name ?? key;

  // The "Gradings in Progress" panel merges two sources: every run in flight
  // deployment-wide (durable — it survives a refresh and shows other people's
  // runs), plus this session's finished runs, so the completion + cost pill
  // doesn't disappear the instant a run ends.
  const activeSlugs = new Set(activeGradeJobs.map((j) => j.target));
  const gradingRows: {
    key: string;
    name: string;
    job: Job;
    startedAt?: string;
    live: boolean;
  }[] = [
    ...activeGradeJobs.map((j) => ({
      key: j.job_id,
      name: nameFor(j.target),
      job: j,
      startedAt: j.created_at,
      live: true,
    })),
    ...jobEntries
      .filter(
        ([key, j]) =>
          (j.status === "succeeded" || j.status === "failed") && !activeSlugs.has(key),
      )
      .map(([key, j]) => ({ key, name: nameFor(key), job: j, live: false })),
  ];
  // Staff: select + rank + 9 data columns. Students lose the select column
  // plus Project Space / Project / Last Graded (leaderboard view).
  const colCount = canGrade ? 11 : 7;

  // Drop selections whose student no longer exists (removed elsewhere).
  useEffect(() => {
    setSelectedSlugs((prev) => {
      const alive = new Set(students.map((s) => s.slug));
      const next = new Set([...prev].filter((slug) => alive.has(slug)));
      return next.size === prev.size ? prev : next;
    });
  }, [students]);

  const toggleSelected = (slug: string) =>
    setSelectedSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

  // The students the toolbar actions target.
  const selectedStudents = useMemo(
    () => students.filter((s) => selectedSlugs.has(s.slug)),
    [students, selectedSlugs],
  );
  const selectedBusy = selectedStudents.some(studentBusy);
  const busySelected = selectedStudents.filter(studentBusy);

  // Header checkbox: selects/clears every row shown on the current page.
  const pageSlugs = pageItems.map((s) => s.slug);
  const allPageSelected =
    pageSlugs.length > 0 && pageSlugs.every((slug) => selectedSlugs.has(slug));
  const somePageSelected = pageSlugs.some((slug) => selectedSlugs.has(slug));
  const toggleSelectPage = () =>
    setSelectedSlugs((prev) => {
      const next = new Set(prev);
      if (allPageSelected) pageSlugs.forEach((slug) => next.delete(slug));
      else pageSlugs.forEach((slug) => next.add(slug));
      return next;
    });

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}

      {gradingRows.length > 0 && (
        <Panel
          title="Gradings in Progress"
          hint="Every grading running right now — whoever started it and from wherever — plus the ones this session just finished. This list is read from the server, so it survives a browser refresh, and a student already being graded can't be queued again until their run ends. Full “grade all” runs go through the batch API and typically take a few minutes (occasionally up to an hour); the grades below refresh automatically when a run finishes."
        >
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="plain">Student</th>
                  <th className="plain">Status</th>
                  <th className="plain">Running for</th>
                  <th className="plain">Started by</th>
                </tr>
              </thead>
              <tbody>
                {gradingRows.map(({ key, name, job, startedAt, live }) => (
                  <tr key={key}>
                    <td>{name}</td>
                    <td>
                      <StatusPill job={job} kind="grade" />
                    </td>
                    <td className="cell-muted">
                      {live ? (elapsedSince(startedAt) ?? "—") : "finished"}
                    </td>
                    <td className="cell-muted">{job.requested_by ?? "you"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel
        title="Student Grades of All Projects"
        hint={
          canGrade
            ? "Every graded student project. Click a column header to sort, or a student's name for their detailed evaluation. Tick one or more rows (the checkbox in the header selects the whole page) to enable the Grade, Edit and Remove toolbar icons — hover an icon for its name. Grading one student opens the exercise picker; grading several runs all exercises for each. Edit works on one student at a time and changes their name, email, project space or project — never their grades. The download icon exports the roster (as shown) to CSV."
            : "Every graded student project. Click a column header to sort. Click your own name to open your detailed evaluation — other students' detail pages stay private."
        }
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by student or project"
            />
            <span className="toolbar-spacer" />
            {canGrade && (
              <>
                <button
                  className="tool-btn"
                  onClick={() => {
                    if (selectedStudents.length === 1) {
                      // One student → the scope picker (choose exercises).
                      const s = selectedStudents[0];
                      setScopeFor({ name: s.display_name, slug: s.slug });
                    } else if (selectedStudents.length > 1) {
                      // Several students → bulk "grade all exercises" each.
                      setBulkGrading(selectedStudents);
                    }
                  }}
                  disabled={selectedStudents.length === 0 || selectedBusy}
                  title={
                    selectedStudents.length === 0
                      ? "Grade — select one or more students first"
                      : selectedBusy
                        ? `Grade — a grading is already running for ${busySelected
                            .map((s) => s.display_name)
                            .join(", ")}. Wait for it to finish.`
                        : selectedStudents.length === 1
                          ? "Grade the selected student (pick which exercises)"
                          : `Grade all exercises for the ${selectedStudents.length} selected students`
                  }
                  aria-label="Grade selected students"
                >
                  <IconGrade size={18} />
                </button>
                {isAdmin && (
                  <button
                    className="tool-btn"
                    onClick={() =>
                      selectedStudents.length === 1 && setEditing(selectedStudents[0])
                    }
                    disabled={selectedStudents.length !== 1 || selectedBusy}
                    title={
                      selectedStudents.length === 0
                        ? "Edit — select a student first"
                        : selectedStudents.length > 1
                          ? "Edit — only one student can be edited at a time"
                          : selectedBusy
                            ? `Edit — a grading is already running for ${selectedStudents[0].display_name}. Wait for it to finish.`
                            : "Edit the selected student"
                    }
                    aria-label="Edit selected student"
                  >
                    <IconEdit size={18} />
                  </button>
                )}
                {isAdmin && (
                  <button
                    className="tool-btn danger"
                    onClick={() =>
                      selectedStudents.length > 0 && setRemoving(selectedStudents)
                    }
                    disabled={selectedStudents.length === 0 || selectedBusy}
                    title={
                      selectedStudents.length === 0
                        ? "Remove — select at least one student first"
                        : selectedStudents.length === 1
                          ? "Remove the selected student permanently"
                          : `Remove ${selectedStudents.length} selected students permanently`
                    }
                    aria-label="Remove selected students"
                  >
                    <IconTrash size={18} />
                  </button>
                )}
                <button
                  className="tool-btn"
                  onClick={() => setAdding(true)}
                  title="Add student"
                  aria-label="Add student"
                >
                  <IconPlus size={18} />
                </button>
                <button
                  className="tool-btn"
                  onClick={() => downloadRosterCsv(visible, activeExercises.length)}
                  disabled={visible.length === 0}
                  title="Export the roster (as shown) to CSV"
                  aria-label="Export roster to CSV"
                >
                  <IconDownload size={18} />
                </button>
              </>
            )}
            <label className="field">
              Entries per Page:
              <select
                value={perPage}
                onChange={(e) => {
                  setPerPage(Number(e.target.value));
                  setPage(1);
                }}
              >
                <option value={10}>10</option>
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </label>
          </>
        }
        footer={
          <PagerFooter
            page={page}
            pageCount={pageCount}
            onPage={setPage}
            lastUpdated={lastUpdated}
          />
        }
      >
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                {canGrade && (
                  <th className="plain select-col">
                    <RowCheckbox
                      checked={allPageSelected}
                      indeterminate={somePageSelected}
                      onChange={toggleSelectPage}
                      ariaLabel="Select all rows on this page"
                      disabled={pageSlugs.length === 0}
                    />
                  </th>
                )}
                <th className="plain rank-col" aria-label="Rank" />
                <SortableTh label="Student" sortKey="student" sort={sort} onSort={onSort} />
                {canGrade && (
                  <SortableTh label="Project Space" sortKey="space" sort={sort} onSort={onSort} />
                )}
                {canGrade && (
                  <SortableTh label="Project" sortKey="project" sort={sort} onSort={onSort} />
                )}
                <SortableTh label="Total Points" sortKey="points" sort={sort} onSort={onSort} />
                <SortableTh label="Pass" sortKey="pass" sort={sort} onSort={onSort} />
                <SortableTh label="Fail" sortKey="fail" sort={sort} onSort={onSort} />
                <SortableTh label="Missing" sortKey="missing" sort={sort} onSort={onSort} />
                <SortableTh label="Not Graded" sortKey="notgraded" sort={sort} onSort={onSort} />
                {canGrade && (
                  <SortableTh label="Last Graded" sortKey="graded" sort={sort} onSort={onSort} />
                )}
              </tr>
            </thead>
            <tbody>
              {pageItems.map((s, i) => {
                const c = s.counts ?? { pass: 0, fail: 0, missing: 0, needs_sync: 0 };
                const needsSync = c.needs_sync ?? c.needs_prep ?? 0;
                const earned = s.points_earned ?? 0;
                const possible = s.points_possible ?? 0;
                const tier = tierForRatio(earned, possible);
                const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;
                // Registered exercises this student has no verdict for at
                // all — never graded, or the exercise was added later.
                const gradedCount = c.pass + c.fail + c.missing + needsSync;
                const notGraded =
                  activeExercises.length > 0
                    ? Math.max(0, activeExercises.length - gradedCount)
                    : 0;
                // Position under the current sort, continuous across pages:
                // the top row is always #1 whatever column is sorted.
                const rank = (page - 1) * perPage + i + 1;
                return (
                  <tr
                    key={s.slug}
                    className={selectedSlugs.has(s.slug) ? "row-selected" : undefined}
                  >
                    {canGrade && (
                      <td className="select-cell">
                        <RowCheckbox
                          checked={selectedSlugs.has(s.slug)}
                          onChange={() => toggleSelected(s.slug)}
                          ariaLabel={`Select ${s.display_name}`}
                        />
                      </td>
                    )}
                    <td className="rank-cell">
                      <RankBadge rank={rank} />
                    </td>
                    <td className={sc("student")}>
                      {canOpen(s) ? (
                        <Link to={`/students/${encodeURIComponent(s.slug)}`}>
                          {s.display_name}
                        </Link>
                      ) : (
                        s.display_name
                      )}
                      {/* Marks the row as locked by a run in flight — the
                          Grade button is disabled for it either way. */}
                      {canGrade && activeGradingFor(s.slug) && (
                        <span
                          className="status-pill running row-grading"
                          title={`Grading in progress — started by ${
                            activeGradingFor(s.slug)?.requested_by ?? "someone"
                          }${
                            elapsedSince(activeGradingFor(s.slug)?.created_at)
                              ? `, ${elapsedSince(activeGradingFor(s.slug)?.created_at)} ago`
                              : ""
                          }`}
                        >
                          <span className="spinner" />
                          Grading…
                        </span>
                      )}
                    </td>
                    {canGrade && (
                      <td className={`${sc("space")} cell-mono`}>{s.space ?? "—"}</td>
                    )}
                    {canGrade && (
                      <td
                        className={`${sc("project")} cell-mono`}
                        title={
                          s.project && s.project !== s.display_name
                            ? "SnapLogic project grading looks in (differs from the student name)"
                            : "Defaults to the student name"
                        }
                      >
                        {s.project ?? s.display_name}
                      </td>
                    )}
                    <td className={sc("points")}>
                      <span className={`pts-chip tier-${tier}`}>
                        {earned}/{possible} pts
                        {pct !== null && <span className="pct">({pct}%)</span>}
                      </span>
                      {needsSync > 0 && (
                        <span
                          className="warn-chip"
                          title={`${needsSync} exercise${needsSync === 1 ? " was" : "s were"} skipped because its grading artifacts are not synced. Sync them on the Exercises page, then regrade.`}
                        >
                          ⚠
                        </span>
                      )}
                    </td>
                    <td className={sc("pass")}>
                      <Count n={c.pass} kind="pass" />
                    </td>
                    <td className={sc("fail")}>
                      <Count n={c.fail} kind="fail" />
                    </td>
                    <td className={sc("missing")}>
                      <Count n={c.missing} kind="missing" />
                    </td>
                    <td
                      className={sc("notgraded")}
                      title={
                        notGraded > 0
                          ? `${notGraded} registered exercise${notGraded === 1 ? " has" : "s have"} never been graded for this student. Open the student to grade ${notGraded === 1 ? "it" : "them"} individually.`
                          : undefined
                      }
                    >
                      <Count n={notGraded} kind="notgraded" />
                    </td>
                    {canGrade && (
                      <td className={`${sc("graded")} cell-muted`}>
                        {s.graded_at ?? "—"}
                      </td>
                    )}
                  </tr>
                );
              })}
              {!loading && visible.length === 0 && (
                <tr>
                  <td colSpan={colCount} className="empty-cell">
                    <h3>No students yet</h3>
                    {canGrade ? (
                      <>
                        Use the + icon in the toolbar above to register a
                        student (their SnapLogic project must already exist),
                        then tick their row and start a grading with the Grade
                        icon.
                      </>
                    ) : (
                      <>Nothing has been graded yet — check back later.</>
                    )}
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td colSpan={colCount} className="empty-cell">
                    Loading…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {adding && (
        <StudentModal
          defaultSpace={defaultSpace}
          onSubmit={registerOnly}
          onClose={() => setAdding(false)}
        />
      )}

      {editing && isAdmin && (
        <StudentModal
          defaultSpace={defaultSpace}
          initial={editing}
          onSubmit={(name, space, project, email) =>
            saveStudent(editing, name, space, project, email)
          }
          onClose={() => setEditing(null)}
        />
      )}

      {scopeFor && (
        <GradeScopeModal
          studentName={scopeFor.name}
          exercises={activeExercises}
          onStart={(tasks) => {
            const { name, slug } = scopeFor;
            setScopeFor(null);
            void startGrade(name, slug, tasks);
          }}
          onClose={() => setScopeFor(null)}
        />
      )}

      {bulkGrading && bulkGrading.length > 0 && (
        <ConfirmModal
          title="Grade Multiple Students"
          confirmLabel={`Grade all exercises for ${bulkGrading.length} students`}
          confirmClassName="btn primary"
          busyLabel="Queuing…"
          onConfirm={() => bulkGrade(bulkGrading)}
          onClose={() => setBulkGrading(null)}
        >
          <p>
            Queue a full <strong>grade-all-exercises</strong> run for each of these{" "}
            <strong>{bulkGrading.length} students</strong>? Each runs as its own job
            (the worker grades them one at a time).
          </p>
          <ul className="bulk-list">
            {bulkGrading.map((s) => (
              <li key={s.slug}>{s.display_name}</li>
            ))}
          </ul>
          <p className="hint">
            Each student's grading spends Claude tokens — full runs use the
            50%-cheaper Batch API, but this still multiplies the per-student cost
            by {bulkGrading.length}.
          </p>
        </ConfirmModal>
      )}

      {removing && removing.length > 0 && isAdmin && (
        <ConfirmModal
          title={removing.length === 1 ? "Remove Student" : "Remove Students"}
          confirmLabel={
            removing.length === 1
              ? `Remove ${removing[0].display_name}`
              : `Remove ${removing.length} students`
          }
          busyLabel="Removing…"
          onConfirm={() => removeStudents(removing)}
          onClose={() => setRemoving(null)}
        >
          {removing.length === 1 ? (
            <p>
              Permanently remove <strong>{removing[0].display_name}</strong>?
              This deletes their grades, their full grading history, and all of
              their records. Their SnapLogic project is left untouched.
            </p>
          ) : (
            <>
              <p>
                Permanently remove these <strong>{removing.length} students</strong>?
                This deletes their grades, their full grading history, and all
                of their records. Their SnapLogic projects are left untouched.
              </p>
              <ul className="bulk-list">
                {removing.map((s) => (
                  <li key={s.slug}>{s.display_name}</li>
                ))}
              </ul>
            </>
          )}
          <p className="hint">This cannot be undone.</p>
        </ConfirmModal>
      )}
    </main>
  );
}
