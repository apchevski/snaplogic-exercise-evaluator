import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, pollJob } from "../api";
import { useToken } from "../auth";
import { StatusPill } from "../components/StatusPill";
import {
  PagerFooter,
  Panel,
  SearchBox,
  SortableTh,
  nextSort,
  usePagination,
  type SortState,
} from "../components/table";
import { tierForRatio } from "../components/TaskCard";
import type { Job, StudentMeta } from "../types";

const COMPARE: Record<string, (a: StudentMeta, b: StudentMeta) => number> = {
  student: (a, b) => a.display_name.localeCompare(b.display_name),
  space: (a, b) => (a.space ?? "").localeCompare(b.space ?? ""),
  points: (a, b) => (a.points_earned ?? 0) - (b.points_earned ?? 0),
  pass: (a, b) => (a.counts?.pass ?? 0) - (b.counts?.pass ?? 0),
  fail: (a, b) => (a.counts?.fail ?? 0) - (b.counts?.fail ?? 0),
  missing: (a, b) => (a.counts?.missing ?? 0) - (b.counts?.missing ?? 0),
  needs_prep: (a, b) => (a.counts?.needs_prep ?? 0) - (b.counts?.needs_prep ?? 0),
  graded: (a, b) => (a.graded_at ?? "").localeCompare(b.graded_at ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  student: "asc",
  space: "asc",
  points: "desc",
  pass: "desc",
  fail: "desc",
  missing: "desc",
  needs_prep: "desc",
  graded: "desc",
};

function Count({ n, kind }: { n: number; kind: string }) {
  return <span className={n > 0 ? `count-${kind}` : "count-zero"}>{n}</span>;
}

export default function Dashboard() {
  const token = useToken();
  const [students, setStudents] = useState<StudentMeta[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "points", dir: "desc" });
  const [perPage, setPerPage] = useState(25);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [newStudent, setNewStudent] = useState("");
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

  const startGrade = useCallback(
    async (studentName: string, slugHint?: string) => {
      const key = slugHint ?? studentName;
      setError(null);
      try {
        const { id } = await api.startGrading(token, studentName);
        const job = await pollJob(
          () => api.getGrading(token, id),
          (j) => setJobs((prev) => ({ ...prev, [key]: j })),
        );
        if (job.status === "succeeded") void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, refresh],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = students.filter(
      (s) =>
        !q ||
        s.display_name.toLowerCase().includes(q) ||
        (s.space ?? "").toLowerCase().includes(q),
    );
    const cmp = COMPARE[sort.key] ?? COMPARE.points;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => cmp(a, b) * sign);
  }, [students, search, sort]);

  const { page, setPage, pageItems, pageCount } = usePagination(visible, perPage);

  const jobBusy = (key: string) => {
    const j = jobs[key];
    return !!j && (j.status === "queued" || j.status === "running");
  };

  const toggleExpanded = (slug: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

  const onSort = (key: string) => setSort((s) => nextSort(s, key, DEFAULT_DIR[key] ?? "asc"));
  const sc = (key: string) => (sort.key === key ? "sorted" : "");

  const jobEntries = Object.entries(jobs);
  const nameFor = (key: string) =>
    students.find((s) => s.slug === key || s.display_name === key)?.display_name ?? key;

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}

      {jobEntries.length > 0 && (
        <Panel
          title="Grading Jobs of This Session"
          hint="Jobs started from this browser session. Finished jobs refresh the grades below."
        >
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="plain">Student</th>
                  <th className="plain">Status</th>
                </tr>
              </thead>
              <tbody>
                {jobEntries.map(([key, job]) => (
                  <tr key={key}>
                    <td>{nameFor(key)}</td>
                    <td>
                      <StatusPill job={job} kind="grade" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel
        title="Student Grades of All Projects"
        hint="Every graded student project. Click a column header to sort, or a row's + to see the overall summary."
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by student or project space"
            />
            <span className="toolbar-spacer" />
            <form
              className="grade-new"
              onSubmit={(e) => {
                e.preventDefault();
                const name = newStudent.trim();
                if (name) {
                  setNewStudent("");
                  void startGrade(name);
                }
              }}
            >
              <input
                placeholder="Grade a new student (project name)…"
                value={newStudent}
                onChange={(e) => setNewStudent(e.target.value)}
              />
              <button className="btn primary" type="submit" disabled={!newStudent.trim()}>
                Grade New
              </button>
            </form>
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
                <th className="plain" aria-label="Expand" />
                <SortableTh label="Student" sortKey="student" sort={sort} onSort={onSort} />
                <SortableTh label="Project Space" sortKey="space" sort={sort} onSort={onSort} />
                <SortableTh label="Total Points" sortKey="points" sort={sort} onSort={onSort} />
                <SortableTh label="Pass" sortKey="pass" sort={sort} onSort={onSort} />
                <SortableTh label="Fail" sortKey="fail" sort={sort} onSort={onSort} />
                <SortableTh label="Missing" sortKey="missing" sort={sort} onSort={onSort} />
                <SortableTh label="Needs Prep" sortKey="needs_prep" sort={sort} onSort={onSort} />
                <SortableTh label="Last Graded" sortKey="graded" sort={sort} onSort={onSort} />
                <th className="plain">Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((s) => {
                const c = s.counts ?? { pass: 0, fail: 0, missing: 0, needs_prep: 0 };
                const earned = s.points_earned ?? 0;
                const possible = s.points_possible ?? 0;
                const tier = tierForRatio(earned, possible);
                const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;
                const isOpen = expanded.has(s.slug);
                return (
                  <Fragment key={s.slug}>
                    <tr>
                      <td>
                        {s.overall_summary && (
                          <button
                            className="expander"
                            onClick={() => toggleExpanded(s.slug)}
                            aria-expanded={isOpen}
                            aria-label={isOpen ? "Hide summary" : "Show summary"}
                          >
                            {isOpen ? "−" : "+"}
                          </button>
                        )}
                      </td>
                      <td className={sc("student")}>
                        <Link to={`/students/${encodeURIComponent(s.slug)}`}>
                          {s.display_name}
                        </Link>
                      </td>
                      <td className={`${sc("space")} cell-mono`}>{s.space ?? "—"}</td>
                      <td className={sc("points")}>
                        <span className={`pts-chip tier-${tier}`}>
                          {earned}/{possible} pts
                          {pct !== null && <span className="pct">({pct}%)</span>}
                        </span>
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
                      <td className={sc("needs_prep")}>
                        <Count n={c.needs_prep} kind="needs_prep" />
                      </td>
                      <td className={`${sc("graded")} cell-muted`}>
                        {s.graded_at ?? "never graded"}
                      </td>
                      <td>
                        <span className="actions-cell">
                          <button
                            className="btn small"
                            onClick={() => void startGrade(s.display_name, s.slug)}
                            disabled={jobBusy(s.slug) || jobBusy(s.display_name)}
                          >
                            Grade
                          </button>
                        </span>
                      </td>
                    </tr>
                    {isOpen && s.overall_summary && (
                      <tr className="expand-row">
                        <td colSpan={10}>{s.overall_summary}</td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {!loading && visible.length === 0 && (
                <tr>
                  <td colSpan={10} className="empty-cell">
                    <h3>No graded students yet</h3>
                    Use “Grade a new student” above to run the first grading.
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td colSpan={10} className="empty-cell">
                    Loading…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </main>
  );
}
