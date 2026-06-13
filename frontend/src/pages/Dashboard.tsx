import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, pollJob } from "../api";
import { useToken } from "../auth";
import { StatusPill } from "../components/StatusPill";
import { tierForRatio } from "../components/TaskCard";
import type { Job, StudentMeta } from "../types";

type SortKey = "points-desc" | "points-asc" | "passes-desc" | "name-asc" | "date-desc";

function StudentCard({
  student,
  job,
  onGrade,
  busy,
}: {
  student: StudentMeta;
  job: Job | null;
  onGrade: () => void;
  busy: boolean;
}) {
  const c = student.counts ?? { pass: 0, fail: 0, missing: 0, needs_prep: 0 };
  const earned = student.points_earned ?? 0;
  const possible = student.points_possible ?? 0;
  const tier = tierForRatio(earned, possible);
  const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;
  return (
    <article className="student-card">
      <header>
        <h2>
          <Link to={`/students/${encodeURIComponent(student.slug)}`}>
            {student.display_name}
          </Link>
        </h2>
        <div className="meta">
          {student.space && <span className="ps">{student.space}</span>}
          {student.space && " · "}
          <span>{student.graded_at ?? "never graded"}</span>
        </div>
      </header>
      <div className="total-row">
        <span className={`total-badge tier-${tier}`}>
          Total: {earned}/{possible} pts
          {pct !== null && <span className="pct">({pct}%)</span>}
        </span>
        <button className="btn primary" onClick={onGrade} disabled={busy}>
          Grade
        </button>
        {job && <StatusPill job={job} kind="grade" />}
      </div>
      <div className="badges">
        <span className="badge pass">{c.pass} pass</span>
        <span className="badge fail">{c.fail} fail</span>
        <span className="badge missing">{c.missing} missing</span>
        <span className="badge needs-prep">{c.needs_prep} needs prep</span>
      </div>
      {student.overall_summary && <p className="overall">{student.overall_summary}</p>}
    </article>
  );
}

export default function Dashboard() {
  const token = useToken();
  const [students, setStudents] = useState<StudentMeta[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("points-desc");
  const [newStudent, setNewStudent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { students } = await api.listStudents(token);
      setStudents(students);
      setError(null);
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
      (s) => !q || s.display_name.toLowerCase().includes(q),
    );
    const by = {
      "points-desc": (a: StudentMeta, b: StudentMeta) =>
        (b.points_earned ?? 0) - (a.points_earned ?? 0),
      "points-asc": (a: StudentMeta, b: StudentMeta) =>
        (a.points_earned ?? 0) - (b.points_earned ?? 0),
      "passes-desc": (a: StudentMeta, b: StudentMeta) =>
        (b.counts?.pass ?? 0) - (a.counts?.pass ?? 0),
      "name-asc": (a: StudentMeta, b: StudentMeta) =>
        a.display_name.localeCompare(b.display_name),
      "date-desc": (a: StudentMeta, b: StudentMeta) =>
        (b.graded_at ?? "").localeCompare(a.graded_at ?? ""),
    }[sort];
    return [...filtered].sort(by);
  }, [students, search, sort]);

  const jobBusy = (key: string) => {
    const j = jobs[key];
    return !!j && (j.status === "queued" || j.status === "running");
  };

  return (
    <>
      <div className="controls">
        <input
          type="search"
          placeholder="Search by student name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
          <option value="points-desc">Highest total points</option>
          <option value="points-asc">Lowest total points</option>
          <option value="passes-desc">Most passes</option>
          <option value="name-asc">Name (A → Z)</option>
          <option value="date-desc">Most recently graded</option>
        </select>
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
            Grade new
          </button>
        </form>
      </div>
      <main>
        {error && <div className="error-banner">{error}</div>}
        {Object.entries(jobs)
          .filter(([key]) => !students.some((s) => s.slug === key || s.display_name === key))
          .map(([key, job]) => (
            <article className="student-card" key={key}>
              <header>
                <h2>{key}</h2>
              </header>
              <div className="total-row">
                <StatusPill job={job} kind="grade" />
              </div>
            </article>
          ))}
        {visible.map((s) => (
          <StudentCard
            key={s.slug}
            student={s}
            job={jobs[s.slug] ?? jobs[s.display_name] ?? null}
            busy={jobBusy(s.slug) || jobBusy(s.display_name)}
            onGrade={() => void startGrade(s.display_name, s.slug)}
          />
        ))}
        {!loading && visible.length === 0 && (
          <div className="empty-state">
            <h2>No graded students yet</h2>
            <p>Use “Grade new student” above to run the first grading.</p>
          </div>
        )}
      </main>
    </>
  );
}
