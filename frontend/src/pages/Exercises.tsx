import { useCallback, useEffect, useMemo, useState } from "react";

import { api, pollJob } from "../api";
import { useIsAdmin, useToken } from "../auth";
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
import type { Exercise, Job } from "../types";

const COMPARE: Record<string, (a: Exercise, b: Exercise) => number> = {
  exercise: (a, b) => (a.title ?? a.slug).localeCompare(b.title ?? b.slug),
  slug: (a, b) => a.slug.localeCompare(b.slug),
  type: (a, b) => (a.task_type ?? "").localeCompare(b.task_type ?? ""),
  status: (a, b) => a.prep_status.localeCompare(b.prep_status),
  prepped: (a, b) => (a.last_prepped_at ?? "").localeCompare(b.last_prepped_at ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  exercise: "asc",
  slug: "asc",
  type: "asc",
  status: "asc",
  prepped: "desc",
};

export default function Exercises() {
  const token = useToken();
  const isAdmin = useIsAdmin();
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "exercise", dir: "asc" });
  const [perPage, setPerPage] = useState(25);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { exercises } = await api.listExercises(token);
      setExercises(exercises);
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

  const startPrep = useCallback(
    async (slug?: string) => {
      const key = slug ?? "__all__";
      setError(null);
      try {
        const { id } = await api.startPrep(token, slug);
        const job = await pollJob(
          () => api.getPrep(token, id),
          (j) => setJobs((prev) => ({ ...prev, [key]: j })),
        );
        if (job.status === "succeeded") void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, refresh],
  );

  const anyBusy = Object.values(jobs).some(
    (j) => j.status === "queued" || j.status === "running",
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = exercises.filter(
      (ex) =>
        !q ||
        (ex.title ?? "").toLowerCase().includes(q) ||
        ex.slug.toLowerCase().includes(q),
    );
    const cmp = COMPARE[sort.key] ?? COMPARE.exercise;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => cmp(a, b) * sign);
  }, [exercises, search, sort]);

  const { page, setPage, pageItems, pageCount } = usePagination(visible, perPage);

  const onSort = (key: string) => setSort((s) => nextSort(s, key, DEFAULT_DIR[key] ?? "asc"));
  const sc = (key: string) => (sort.key === key ? "sorted" : "");
  const colCount = isAdmin ? 6 : 5;

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      <Panel
        title="Exercise Prep Status of All Projects"
        hint="Every authored exercise and whether its grading artifacts are prepped and current."
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by exercise title or slug"
            />
            <span className="toolbar-spacer" />
            {isAdmin && (
              <>
                {jobs["__all__"] && <StatusPill job={jobs["__all__"]} kind="prep" />}
                <button
                  className="btn primary"
                  onClick={() => void startPrep()}
                  disabled={anyBusy}
                >
                  Prep All Exercises
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
                <SortableTh label="Exercise" sortKey="exercise" sort={sort} onSort={onSort} />
                <SortableTh label="Slug" sortKey="slug" sort={sort} onSort={onSort} />
                <SortableTh label="Task Type" sortKey="type" sort={sort} onSort={onSort} />
                <SortableTh label="Prep Status" sortKey="status" sort={sort} onSort={onSort} />
                <SortableTh label="Last Prepped" sortKey="prepped" sort={sort} onSort={onSort} />
                {isAdmin && <th className="plain">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {pageItems.map((ex) => (
                <tr key={ex.slug}>
                  <td className={sc("exercise")}>{ex.title ?? ex.slug}</td>
                  <td className={`${sc("slug")} cell-mono`}>{ex.slug}</td>
                  <td className={`${sc("type")} cell-muted`}>{ex.task_type ?? "—"}</td>
                  <td className={sc("status")}>
                    <span className={`prep-status ${ex.prep_status}`}>
                      {ex.prep_status.replace(/_/g, " ")}
                    </span>{" "}
                    {ex.missing_from_image && (
                      <span className="prep-status config_error">missing from image</span>
                    )}
                  </td>
                  <td className={`${sc("prepped")} cell-muted`}>
                    {ex.last_prepped_at ?? "never"}
                  </td>
                  {isAdmin && (
                    <td>
                      <span className="actions-cell">
                        <button
                          className="btn small"
                          onClick={() => void startPrep(ex.slug)}
                          disabled={anyBusy}
                        >
                          Prep
                        </button>
                        {jobs[ex.slug] && <StatusPill job={jobs[ex.slug]} kind="prep" />}
                      </span>
                    </td>
                  )}
                </tr>
              ))}
              {!loading && visible.length === 0 && (
                <tr>
                  <td colSpan={colCount} className="empty-cell">
                    <h3>No exercises found</h3>
                    Authored exercise folders ship in the backend image.
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
    </main>
  );
}
