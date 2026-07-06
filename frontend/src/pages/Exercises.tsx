import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, pollJob } from "../api";
import { useIsAdmin, useToken } from "../auth";
import { ConfirmDeleteModal } from "../components/ConfirmDeleteModal";
import { ExerciseModal } from "../components/ExerciseModal";
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
import type { Exercise, ExerciseDetail, Job } from "../types";

const COMPARE: Record<string, (a: Exercise, b: Exercise) => number> = {
  exercise: (a, b) => (a.title ?? a.slug).localeCompare(b.title ?? b.slug),
  type: (a, b) => (a.task_type ?? "").localeCompare(b.task_type ?? ""),
  status: (a, b) => a.prep_status.localeCompare(b.prep_status),
  prepped: (a, b) => (a.last_prepped_at ?? "").localeCompare(b.last_prepped_at ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  exercise: "asc",
  type: "asc",
  status: "asc",
  prepped: "desc",
};

/** "4017654" → "3.8 MB" — chip labels stay short. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Lightweight description.md renderer: the leading H1 is skipped (it
 * duplicates the exercise title), `##`+ headings become sub-headings, and
 * everything else keeps its line breaks (numbered steps, JSON snippets). */
function DescriptionBody({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i++;
  if (lines[i]?.startsWith("# ")) i++;
  const blocks: ReactNode[] = [];
  let buf: string[] = [];
  const flush = () => {
    const chunk = buf.join("\n").trim();
    if (chunk) blocks.push(<p key={blocks.length}>{chunk}</p>);
    buf = [];
  };
  for (; i < lines.length; i++) {
    const heading = /^#{2,}\s+(.*)$/.exec(lines[i].trim());
    if (heading) {
      flush();
      blocks.push(<h4 key={blocks.length}>{heading[1]}</h4>);
    } else {
      buf.push(lines[i]);
    }
  }
  flush();
  return <div className="task-description">{blocks}</div>;
}

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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<ExerciseDetail | null>(null);
  const [editLoading, setEditLoading] = useState<string | null>(null);
  const [archiving, setArchiving] = useState<string | null>(null);
  // Confirmation dialog target for the admin-only permanent Delete.
  const [deleting, setDeleting] = useState<Exercise | null>(null);

  const toggleExpanded = (slug: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

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

  const openEdit = useCallback(
    async (slug: string) => {
      setEditLoading(slug);
      setError(null);
      try {
        const { exercise } = await api.getExercise(token, slug);
        setEditing(exercise);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setEditLoading(null);
      }
    },
    [token],
  );

  const toggleArchived = useCallback(
    async (ex: Exercise) => {
      const archive = !ex.archived;
      if (
        archive &&
        !window.confirm(
          `Archive "${ex.title ?? ex.slug}"? It stops being prepped, graded and counted ` +
            `toward student totals. Nothing is deleted — you can unarchive it anytime.`,
        )
      ) {
        return;
      }
      setArchiving(ex.slug);
      setError(null);
      try {
        await api.updateExercise(token, ex.slug, { archived: archive });
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setArchiving(null);
      }
    },
    [token, refresh],
  );

  // Permanent removal (admin only): the API purges the exercise's S3 content
  // and records and scrubs its result from every student's report. Errors
  // propagate to the confirmation dialog, which stays open and shows them.
  const deleteExercise = useCallback(
    async (slug: string) => {
      await api.deleteExercise(token, slug);
      setDeleting(null);
      await refresh();
    },
    [token, refresh],
  );

  const [downloading, setDownloading] = useState<Set<string>>(new Set());

  const downloadResource = useCallback(
    async (slug: string, filename: string) => {
      const key = `${slug}/${filename}`;
      setDownloading((prev) => new Set(prev).add(key));
      setError(null);
      try {
        const { url } = await api.getExerciseResourceUrl(token, slug, filename);
        // Presigned S3 URL with Content-Disposition: attachment — navigating
        // to it triggers the download without leaving the page.
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setDownloading((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [token],
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
        hint="Every authored exercise and whether its grading artifacts are prepped and current. Click a task name to view its description; click a file to download its input data."
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by exercise title"
            />
            <span className="toolbar-spacer" />
            {isAdmin && (
              <>
                {jobs["__all__"] && <StatusPill job={jobs["__all__"]} kind="prep" />}
                <button className="btn" onClick={() => setShowAdd(true)} disabled={anyBusy}>
                  Add New Exercise
                </button>
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
                <SortableTh label="Task Type" sortKey="type" sort={sort} onSort={onSort} />
                <th className="plain">Files</th>
                <SortableTh label="Prep Status" sortKey="status" sort={sort} onSort={onSort} />
                <SortableTh label="Last Prepped" sortKey="prepped" sort={sort} onSort={onSort} />
                {isAdmin && <th className="plain">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {pageItems.map((ex) => {
                const isOpen = expanded.has(ex.slug);
                return (
                  <Fragment key={ex.slug}>
                    <tr className={ex.archived ? "row-archived" : undefined}>
                      <td className={sc("exercise")}>
                        {ex.description ? (
                          <button
                            className="title-toggle"
                            onClick={() => toggleExpanded(ex.slug)}
                            aria-expanded={isOpen}
                            aria-label={
                              isOpen ? "Hide task description" : "Show task description"
                            }
                          >
                            <span className="caret" aria-hidden="true">
                              {isOpen ? "▾" : "▸"}
                            </span>
                            {ex.title ?? ex.slug}
                          </button>
                        ) : (
                          (ex.title ?? ex.slug)
                        )}
                      </td>
                      <td className={`${sc("type")} cell-muted`}>{ex.task_type ?? "—"}</td>
                      <td>
                        {ex.resources && ex.resources.length > 0 ? (
                          <span className="resource-list">
                            {ex.resources.map((r) => (
                              <button
                                key={r.filename}
                                className="resource-chip"
                                onClick={() => void downloadResource(ex.slug, r.filename)}
                                disabled={downloading.has(`${ex.slug}/${r.filename}`)}
                                title={`Download ${r.filename} (${formatSize(r.size_bytes)})`}
                              >
                                <span aria-hidden="true">⬇</span> {r.filename}
                                <span className="resource-size">{formatSize(r.size_bytes)}</span>
                              </button>
                            ))}
                          </span>
                        ) : (
                          <span className="cell-muted">—</span>
                        )}
                      </td>
                      <td className={sc("status")}>
                        <span className={`prep-status ${ex.prep_status}`}>
                          {ex.prep_status.replace(/_/g, " ")}
                        </span>{" "}
                        {ex.archived && <span className="prep-status archived">archived</span>}{" "}
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
                              disabled={anyBusy || ex.archived}
                            >
                              Prep
                            </button>
                            <button
                              className="btn small"
                              onClick={() => void openEdit(ex.slug)}
                              disabled={editLoading === ex.slug}
                            >
                              {editLoading === ex.slug ? "…" : "Edit"}
                            </button>
                            <button
                              className="btn small"
                              onClick={() => void toggleArchived(ex)}
                              disabled={archiving === ex.slug || anyBusy}
                            >
                              {ex.archived ? "Unarchive" : "Archive"}
                            </button>
                            <button
                              className="btn small danger"
                              onClick={() => setDeleting(ex)}
                              disabled={anyBusy}
                            >
                              Delete
                            </button>
                            {jobs[ex.slug] && <StatusPill job={jobs[ex.slug]} kind="prep" />}
                          </span>
                        </td>
                      )}
                    </tr>
                    {isOpen && ex.description && (
                      <tr className="expand-row">
                        <td colSpan={colCount}>
                          <DescriptionBody text={ex.description} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
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
      {showAdd && isAdmin && (
        <ExerciseModal
          token={token}
          onClose={() => setShowAdd(false)}
          onSaved={() => void refresh()}
        />
      )}
      {editing && isAdmin && (
        <ExerciseModal
          token={token}
          initial={editing}
          onClose={() => setEditing(null)}
          onSaved={() => void refresh()}
        />
      )}
      {deleting && isAdmin && (
        <ConfirmDeleteModal
          title="Delete Exercise"
          confirmLabel={`Delete ${deleting.title ?? deleting.slug}`}
          onConfirm={() => deleteExercise(deleting.slug)}
          onClose={() => setDeleting(null)}
        >
          <p>
            Permanently delete <strong>{deleting.title ?? deleting.slug}</strong>?
            This removes its description, input files and grading artifacts
            from AWS, and erases its result from every student&rsquo;s report
            (points and totals are recalculated). To keep it around without
            grading it, use Archive instead.
          </p>
          <p className="hint">This cannot be undone.</p>
        </ConfirmDeleteModal>
      )}
    </main>
  );
}
