import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { useToken } from "../auth";
import { IconSync } from "../components/icons";
import {
  PagerFooter,
  Panel,
  SearchBox,
  SortableTh,
  nextSort,
  usePagination,
  type SortState,
} from "../components/table";
import type { Job } from "../types";

// Compact per-job cost/verdict summary from the stored result blob.
function jobDetail(job: Job): string {
  if (job.status === "failed") return job.error ?? "failed";
  const r = job.result;
  if (!r) return "";
  const parts: string[] = [];
  if (r.counts) {
    const c = r.counts;
    const bits = [
      c.pass ? `${c.pass}P` : "",
      c.fail ? `${c.fail}F` : "",
      c.missing ? `${c.missing}M` : "",
    ].filter(Boolean);
    if (bits.length) parts.push(bits.join(" · "));
  }
  if (typeof r.points_earned === "number" && typeof r.points_possible === "number") {
    parts.push(`${r.points_earned}/${r.points_possible} pts`);
  }
  if (r.exercises) parts.push(`${r.exercises.length} exercise${r.exercises.length === 1 ? "" : "s"} synced`);
  if (typeof r.usage?.est_cost_usd === "number") parts.push(`≈ $${r.usage.est_cost_usd.toFixed(2)}`);
  return parts.join(" · ");
}

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "Queued",
  running: "Running",
  batch_processing: "Batch grading",
  succeeded: "Succeeded",
  failed: "Failed",
};

const COMPARE: Record<string, (a: Job, b: Job) => number> = {
  created: (a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""),
  type: (a, b) => a.job_type.localeCompare(b.job_type),
  target: (a, b) => (a.target ?? "").localeCompare(b.target ?? ""),
  status: (a, b) => a.status.localeCompare(b.status),
  by: (a, b) => (a.requested_by ?? "").localeCompare(b.requested_by ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  created: "desc",
  type: "asc",
  target: "asc",
  status: "asc",
  by: "asc",
};

/** Recent grade/sync jobs across the whole deployment (admin/mentor). Unlike
 * the Dashboard's "jobs from this session" panel, this reads the persisted
 * JOB rows, so failures that happened while nobody was watching still show
 * up — with who started them and what they cost. */
export default function Activity() {
  const token = useToken();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "created", dir: "desc" });
  const [perPage, setPerPage] = useState(25);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { jobs } = await api.listJobs(token);
      setJobs(jobs);
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

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = jobs.filter(
      (j) =>
        !q ||
        (j.target ?? "").toLowerCase().includes(q) ||
        (j.requested_by ?? "").toLowerCase().includes(q) ||
        j.job_type.toLowerCase().includes(q) ||
        j.status.toLowerCase().includes(q),
    );
    const cmp = COMPARE[sort.key] ?? COMPARE.created;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort(
      (a, b) => cmp(a, b) * sign || (b.created_at ?? "").localeCompare(a.created_at ?? ""),
    );
  }, [jobs, search, sort]);

  const { page, setPage, pageItems, pageCount } = usePagination(visible, perPage);
  const onSort = (key: string) => setSort((s) => nextSort(s, key, DEFAULT_DIR[key] ?? "asc"));
  const sc = (key: string) => (sort.key === key ? "sorted" : "");

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      <Panel
        title="Activity Logs"
        hint="Every grade and sync job the platform has run recently (newest first), including failures that happened in the background. Shows who started each job and its estimated Claude cost. Jobs are pruned automatically after 90 days."
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by student, exercise, user, or status"
            />
            <span className="toolbar-spacer" />
            <button
              className="tool-btn"
              onClick={() => void refresh()}
              title="Refresh"
              aria-label="Refresh"
            >
              <IconSync size={18} />
            </button>
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
          <PagerFooter page={page} pageCount={pageCount} onPage={setPage} lastUpdated={lastUpdated} />
        }
      >
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortableTh label="Started" sortKey="created" sort={sort} onSort={onSort} />
                <SortableTh label="Type" sortKey="type" sort={sort} onSort={onSort} />
                <SortableTh label="Target" sortKey="target" sort={sort} onSort={onSort} />
                <SortableTh label="Status" sortKey="status" sort={sort} onSort={onSort} />
                <th className="plain">Detail</th>
                <SortableTh label="Started by" sortKey="by" sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {pageItems.map((j) => (
                <tr key={j.job_id}>
                  <td className={`${sc("created")} cell-muted`}>{j.created_at ?? "—"}</td>
                  <td className={sc("type")}>
                    <span className={`job-type job-type-${j.job_type}`}>{j.job_type}</span>
                  </td>
                  <td className={`${sc("target")} cell-mono`}>{j.target || "—"}</td>
                  <td className={sc("status")}>
                    <span className={`status-pill ${j.status}`}>{STATUS_LABEL[j.status]}</span>
                  </td>
                  <td className={j.status === "failed" ? "job-error-cell" : "cell-muted"}>
                    {jobDetail(j) || "—"}
                  </td>
                  <td className={`${sc("by")} cell-muted`}>{j.requested_by ?? "—"}</td>
                </tr>
              ))}
              {!loading && visible.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-cell">
                    <h3>No jobs yet</h3>
                    Grade or sync something and it will show up here.
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td colSpan={6} className="empty-cell">
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
