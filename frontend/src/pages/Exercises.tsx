import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, pollJob } from "../api";
import { useIsAdmin, useToken } from "../auth";
import { ConfirmModal } from "../components/ConfirmModal";
import { ExerciseModal } from "../components/ExerciseModal";
import {
  IconArchive,
  IconEdit,
  IconPlus,
  IconSync,
  IconTrash,
  IconUnarchive,
} from "../components/icons";
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
  status: (a, b) => a.sync_status.localeCompare(b.sync_status),
  synced: (a, b) => (a.last_synced_at ?? "").localeCompare(b.last_synced_at ?? ""),
};
const DEFAULT_DIR: Record<string, "asc" | "desc"> = {
  exercise: "asc",
  type: "asc",
  status: "asc",
  synced: "desc",
};

// The "ready" sync state reads as "synced" (green) in the Status column;
// every other state keeps its diagnostic label (underscores → spaces).
const SYNC_STATUS_LABEL: Record<string, string> = { ready: "synced" };

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
  // Confirmation dialog target for archiving (unarchive is immediate).
  const [archiveTarget, setArchiveTarget] = useState<Exercise | null>(null);
  // Confirmation dialog target for the admin-only permanent Delete.
  const [deleting, setDeleting] = useState<Exercise | null>(null);
  // Confirmation dialog target for sync: "all" = Sync All Exercises,
  // otherwise the single exercise being synced.
  const [syncConfirm, setSyncConfirm] = useState<Exercise | "all" | null>(null);

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

  const startSync = useCallback(
    async (slug?: string) => {
      const key = slug ?? "__all__";
      setError(null);
      try {
        const { id } = await api.startSync(token, slug);
        const job = await pollJob(
          () => api.getSync(token, id),
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

  // Flip an exercise's archived flag. Throws on failure so the archive
  // confirmation dialog can stay open and surface the error; the inline
  // unarchive path catches it into the page banner instead.
  const setArchived = useCallback(
    async (ex: Exercise, archived: boolean) => {
      setArchiving(ex.slug);
      try {
        await api.updateExercise(token, ex.slug, { archived });
        await refresh();
      } finally {
        setArchiving(null);
      }
    },
    [token, refresh],
  );

  // The Archive button confirms first (archiving hides an exercise from
  // grading and student totals); Unarchive is harmless, so it runs inline.
  const onArchiveClick = useCallback(
    (ex: Exercise) => {
      if (ex.archived) {
        setError(null);
        void setArchived(ex, false).catch((e) =>
          setError(e instanceof Error ? e.message : String(e)),
        );
      } else {
        setArchiveTarget(ex);
      }
    },
    [setArchived],
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
        title="Exercise Sync Status of All Projects"
        hint="Every authored exercise and whether its grading artifacts are synced and current. Click a task name to view its description; click a file to download its input data."
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
                {jobs["__all__"] && <StatusPill job={jobs["__all__"]} kind="sync" />}
                <button className="btn" onClick={() => setShowAdd(true)} disabled={anyBusy}>
                  <IconPlus />
                  Add New Exercise
                </button>
                <button
                  className="btn primary"
                  onClick={() => setSyncConfirm("all")}
                  disabled={anyBusy}
                >
                  <IconSync />
                  Sync All Exercises
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
                <SortableTh label="Status" sortKey="status" sort={sort} onSort={onSort} />
                <SortableTh label="Last Synced" sortKey="synced" sort={sort} onSort={onSort} />
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
                        {ex.sync_status === "never_synced" ? (
                          <span className="cell-muted">—</span>
                        ) : (
                          <span className={`sync-status ${ex.sync_status}`}>
                            {SYNC_STATUS_LABEL[ex.sync_status] ?? ex.sync_status.replace(/_/g, " ")}
                          </span>
                        )}{" "}
                        {ex.archived && <span className="sync-status archived">archived</span>}{" "}
                        {ex.missing_from_image && (
                          <span className="sync-status config_error">missing from image</span>
                        )}
                      </td>
                      <td className={`${sc("synced")} cell-muted`}>
                        {ex.last_synced_at ?? "—"}
                      </td>
                      {isAdmin && (
                        <td>
                          <span className="actions-cell">
                            <button
                              className="btn small"
                              onClick={() => setSyncConfirm(ex)}
                              disabled={anyBusy || ex.archived}
                            >
                              <IconSync />
                              Sync
                            </button>
                            <button
                              className="btn small"
                              onClick={() => void openEdit(ex.slug)}
                              disabled={editLoading === ex.slug}
                            >
                              <IconEdit />
                              {editLoading === ex.slug ? "…" : "Edit"}
                            </button>
                            <button
                              className="btn small"
                              onClick={() => onArchiveClick(ex)}
                              disabled={archiving === ex.slug || anyBusy}
                            >
                              {ex.archived ? <IconUnarchive /> : <IconArchive />}
                              {ex.archived ? "Unarchive" : "Archive"}
                            </button>
                            <button
                              className="btn small danger"
                              onClick={() => setDeleting(ex)}
                              disabled={anyBusy}
                            >
                              <IconTrash />
                              Delete
                            </button>
                            {jobs[ex.slug] && <StatusPill job={jobs[ex.slug]} kind="sync" />}
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
      {syncConfirm && isAdmin && (
        <ConfirmModal
          title={syncConfirm === "all" ? "Sync All Exercises" : "Sync Exercise"}
          confirmLabel={syncConfirm === "all" ? "Sync all" : "Sync"}
          confirmIcon={<IconSync />}
          confirmClassName="btn primary"
          busyLabel="Starting…"
          onConfirm={async () => {
            const target = syncConfirm;
            setSyncConfirm(null);
            if (target) void startSync(target === "all" ? undefined : target.slug);
          }}
          onClose={() => setSyncConfirm(null)}
        >
          {syncConfirm === "all" ? (
            <p>
              Get <strong>all active exercises</strong> ready for grading? This
              uses each exercise&rsquo;s current files. It can take a while and
              runs in the background.
            </p>
          ) : (
            <p>
              Get <strong>{syncConfirm.title ?? syncConfirm.slug}</strong> ready
              for grading? This uses its current files and runs in the
              background.
            </p>
          )}
        </ConfirmModal>
      )}
      {archiveTarget && isAdmin && (
        <ConfirmModal
          title="Archive Exercise"
          confirmLabel="Archive"
          confirmIcon={<IconArchive />}
          confirmClassName="btn primary"
          busyLabel="Archiving…"
          onConfirm={() =>
            setArchived(archiveTarget, true).then(() => setArchiveTarget(null))
          }
          onClose={() => setArchiveTarget(null)}
        >
          <p>
            Archive <strong>{archiveTarget.title ?? archiveTarget.slug}</strong>?
            While archived, it won&rsquo;t be graded or counted toward student
            totals.
          </p>
          <p className="hint">
            Nothing is deleted — you can bring it back anytime.
          </p>
        </ConfirmModal>
      )}
      {deleting && isAdmin && (
        <ConfirmModal
          title="Delete Exercise"
          confirmLabel={`Delete ${deleting.title ?? deleting.slug}`}
          confirmIcon={<IconTrash />}
          onConfirm={() => deleteExercise(deleting.slug)}
          onClose={() => setDeleting(null)}
        >
          <p>
            Permanently delete <strong>{deleting.title ?? deleting.slug}</strong>?
            This removes its description, input files, and grades, and clears
            its result from every student&rsquo;s report (points and totals
            update automatically). To keep it without grading it, use Archive
            instead.
          </p>
          <p className="hint">This cannot be undone.</p>
        </ConfirmModal>
      )}
    </main>
  );
}
