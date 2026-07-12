import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, pollJob } from "../api";
import { useIsAdmin, useIsStudentOnly, useToken } from "../auth";
import { ConfirmModal } from "../components/ConfirmModal";
import { ExerciseModal } from "../components/ExerciseModal";
import {
  IconArchive,
  IconCheck,
  IconCheckCircle,
  IconCopy,
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
  RowCheckbox,
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
  // Students get a read-only catalog: descriptions and input files only. The
  // sync machinery (Status / Last Synced columns, archived rows) is staff
  // detail — archived exercises aren't graded or counted, so they're hidden.
  const isStudent = useIsStudentOnly();
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
  const [archiving, setArchiving] = useState(false);
  // Confirmation dialog targets for archiving (unarchive is immediate).
  const [archiveTarget, setArchiveTarget] = useState<Exercise[] | null>(null);
  // Confirmation dialog targets for the admin-only permanent Delete.
  const [deleting, setDeleting] = useState<Exercise[] | null>(null);
  // Confirmation dialog target for sync: "all" = Sync All Exercises,
  // otherwise the selected exercises being synced.
  const [syncConfirm, setSyncConfirm] = useState<Exercise[] | "all" | null>(null);
  // Row selection (admin): the toolbar's Sync/Edit/Archive/Delete buttons act
  // on these exercises. Stored as slugs so a refresh keeps the selection.
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());

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

  // Flip the archived flag on each target, sequentially so a failure names
  // the exercise it hit. Throws on failure so the archive confirmation dialog
  // can stay open and surface the error; the inline unarchive path catches it
  // into the page banner instead.
  const setArchivedMany = useCallback(
    async (targets: Exercise[], archived: boolean) => {
      setArchiving(true);
      try {
        const failures: string[] = [];
        for (const ex of targets) {
          try {
            await api.updateExercise(token, ex.slug, { archived });
          } catch (e) {
            failures.push(
              `${ex.title ?? ex.slug}: ${e instanceof Error ? e.message : String(e)}`,
            );
          }
        }
        await refresh();
        if (failures.length > 0) throw new Error(failures.join(" — "));
      } finally {
        setArchiving(false);
      }
    },
    [token, refresh],
  );

  // Permanent removal (admin only): the API purges each exercise's S3 content
  // and records and scrubs its result from every student's report. Runs
  // sequentially; already-deleted exercises stay deleted. Errors propagate to
  // the confirmation dialog, which stays open and shows them.
  const deleteExercises = useCallback(
    async (targets: Exercise[]) => {
      const failures: string[] = [];
      for (const ex of targets) {
        try {
          await api.deleteExercise(token, ex.slug);
          setSelectedSlugs((prev) => {
            const next = new Set(prev);
            next.delete(ex.slug);
            return next;
          });
        } catch (e) {
          failures.push(
            `${ex.title ?? ex.slug}: ${e instanceof Error ? e.message : String(e)}`,
          );
        }
      }
      await refresh();
      if (failures.length > 0) throw new Error(failures.join(" — "));
      setDeleting(null);
    },
    [token, refresh],
  );

  const [downloading, setDownloading] = useState<Set<string>>(new Set());

  // Which row's copy button just fired — its icon flips to a check briefly.
  const [copiedSlug, setCopiedSlug] = useState<string | null>(null);

  const copyTaskName = useCallback(async (ex: Exercise) => {
    try {
      await navigator.clipboard.writeText(ex.title ?? ex.slug);
      setCopiedSlug(ex.slug);
      window.setTimeout(
        () => setCopiedSlug((cur) => (cur === ex.slug ? null : cur)),
        1500,
      );
    } catch {
      setError("Couldn't copy to the clipboard.");
    }
  }, []);

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
        !(isStudent && ex.archived) &&
        (!q ||
          (ex.title ?? "").toLowerCase().includes(q) ||
          ex.slug.toLowerCase().includes(q)),
    );
    const cmp = COMPARE[sort.key] ?? COMPARE.exercise;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => cmp(a, b) * sign);
  }, [exercises, search, sort, isStudent]);

  const { page, setPage, pageItems, pageCount } = usePagination(visible, perPage);

  const onSort = (key: string) => setSort((s) => nextSort(s, key, DEFAULT_DIR[key] ?? "asc"));
  const sc = (key: string) => (sort.key === key ? "sorted" : "");
  // Admin gets the leading Select column; students lose Status + Last Synced.
  const colCount = isAdmin ? 6 : isStudent ? 3 : 5;

  // Drop selections whose exercise no longer exists (deleted elsewhere).
  useEffect(() => {
    setSelectedSlugs((prev) => {
      const alive = new Set(exercises.map((ex) => ex.slug));
      const next = new Set([...prev].filter((slug) => alive.has(slug)));
      return next.size === prev.size ? prev : next;
    });
  }, [exercises]);

  const toggleSelected = (slug: string) =>
    setSelectedSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

  // The exercises the toolbar actions target.
  const selectedExercises = useMemo(
    () => exercises.filter((ex) => selectedSlugs.has(ex.slug)),
    [exercises, selectedSlugs],
  );
  // Sync skips archived exercises (the backend refuses them anyway).
  const syncTargets = selectedExercises.filter((ex) => !ex.archived);
  // With every active exercise selected, Sync runs the single sync-all job
  // (one backend job and lock) instead of one job per exercise.
  const activeCount = exercises.filter((ex) => !ex.archived).length;
  const allActiveSelected = activeCount > 0 && syncTargets.length === activeCount;
  // Archive/Unarchive needs a uniform selection: all archived → Unarchive,
  // none archived → Archive, a mix → disabled (ambiguous intent).
  const allArchived =
    selectedExercises.length > 0 && selectedExercises.every((ex) => ex.archived);
  const noneArchived =
    selectedExercises.length > 0 && selectedExercises.every((ex) => !ex.archived);

  // Header checkbox: selects/clears every row shown on the current page.
  const pageSlugs = pageItems.map((ex) => ex.slug);
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

  // The Archive button confirms first (archiving hides an exercise from
  // grading and student totals); Unarchive is harmless, so it runs inline.
  const onArchiveClick = () => {
    if (allArchived) {
      setError(null);
      void setArchivedMany(selectedExercises, false).catch((e) =>
        setError(e instanceof Error ? e.message : String(e)),
      );
    } else if (noneArchived) {
      setArchiveTarget(selectedExercises);
    }
  };

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}
      <Panel
        title={isStudent ? "Exercises" : "Exercise Sync Status of All Projects"}
        hint={
          isStudent
            ? "Every exercise in the course. Click a task name to view its description; click a file to download its input data."
            : "Every authored exercise and whether its grading artifacts are synced and current. Click a task name to view its description; click a file to download its input data. Tick one or more rows (the checkbox in the header selects the whole page) to enable the Sync, Edit, Archive and Delete toolbar icons — hover an icon for its name. Edit works on one exercise at a time; selecting every exercise makes Sync sync them all."
        }
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
                <button
                  className="tool-btn"
                  onClick={() =>
                    syncTargets.length > 0 &&
                    setSyncConfirm(allActiveSelected ? "all" : syncTargets)
                  }
                  disabled={syncTargets.length === 0 || anyBusy}
                  title={
                    selectedExercises.length === 0
                      ? "Sync — select at least one exercise first (selecting every exercise syncs them all)"
                      : syncTargets.length === 0
                        ? "Sync — archived exercises can't be synced"
                        : allActiveSelected
                          ? "Sync all exercises"
                          : syncTargets.length === 1
                            ? "Sync the selected exercise"
                            : `Sync ${syncTargets.length} selected exercises`
                  }
                  aria-label="Sync selected exercises"
                >
                  <IconSync size={18} />
                </button>
                <button
                  className="tool-btn"
                  onClick={() =>
                    selectedExercises.length === 1 &&
                    void openEdit(selectedExercises[0].slug)
                  }
                  disabled={selectedExercises.length !== 1 || editLoading !== null}
                  title={
                    selectedExercises.length === 0
                      ? "Edit — select an exercise first"
                      : selectedExercises.length > 1
                        ? "Edit — only one exercise can be edited at a time"
                        : "Edit the selected exercise"
                  }
                  aria-label="Edit selected exercise"
                >
                  <IconEdit size={18} />
                </button>
                <button
                  className="tool-btn"
                  onClick={onArchiveClick}
                  disabled={
                    (!allArchived && !noneArchived) || archiving || anyBusy
                  }
                  title={
                    selectedExercises.length === 0
                      ? "Archive — select at least one exercise first"
                      : !allArchived && !noneArchived
                        ? "Archive — selection mixes archived and active exercises, select one kind"
                        : `${allArchived ? "Unarchive" : "Archive"} ${
                            selectedExercises.length === 1
                              ? "the selected exercise"
                              : `${selectedExercises.length} selected exercises`
                          }`
                  }
                  aria-label={
                    allArchived
                      ? "Unarchive selected exercises"
                      : "Archive selected exercises"
                  }
                >
                  {allArchived ? <IconUnarchive size={18} /> : <IconArchive size={18} />}
                </button>
                <button
                  className="tool-btn danger"
                  onClick={() =>
                    selectedExercises.length > 0 && setDeleting(selectedExercises)
                  }
                  disabled={selectedExercises.length === 0 || anyBusy}
                  title={
                    selectedExercises.length === 0
                      ? "Delete — select at least one exercise first"
                      : selectedExercises.length === 1
                        ? "Delete the selected exercise permanently"
                        : `Delete ${selectedExercises.length} selected exercises permanently`
                  }
                  aria-label="Delete selected exercises"
                >
                  <IconTrash size={18} />
                </button>
                <button
                  className="tool-btn"
                  onClick={() => setShowAdd(true)}
                  disabled={anyBusy}
                  title="Add new exercise"
                  aria-label="Add new exercise"
                >
                  <IconPlus size={18} />
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
                {isAdmin && (
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
                <SortableTh label="Exercise" sortKey="exercise" sort={sort} onSort={onSort} />
                <SortableTh label="Task Type" sortKey="type" sort={sort} onSort={onSort} />
                <th className="plain">Files</th>
                {!isStudent && (
                  <>
                    <SortableTh label="Sync Status" sortKey="status" sort={sort} onSort={onSort} />
                    <SortableTh label="Last Synced" sortKey="synced" sort={sort} onSort={onSort} />
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {pageItems.map((ex) => {
                const isOpen = expanded.has(ex.slug);
                return (
                  <Fragment key={ex.slug}>
                    <tr
                      className={
                        [
                          ex.archived ? "row-archived" : "",
                          selectedSlugs.has(ex.slug) ? "row-selected" : "",
                        ]
                          .filter(Boolean)
                          .join(" ") || undefined
                      }
                    >
                      {isAdmin && (
                        <td className="select-cell">
                          <RowCheckbox
                            checked={selectedSlugs.has(ex.slug)}
                            onChange={() => toggleSelected(ex.slug)}
                            ariaLabel={`Select ${ex.title ?? ex.slug}`}
                          />
                        </td>
                      )}
                      <td className={sc("exercise")}>
                        <span className="exercise-cell">
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
                          <button
                            type="button"
                            className={`copy-btn${copiedSlug === ex.slug ? " copied" : ""}`}
                            onClick={() => void copyTaskName(ex)}
                            title={copiedSlug === ex.slug ? "Copied!" : "Copy task name"}
                            aria-label={`Copy task name "${ex.title ?? ex.slug}"`}
                          >
                            {copiedSlug === ex.slug ? (
                              <IconCheck size={13} />
                            ) : (
                              <IconCopy size={13} />
                            )}
                          </button>
                        </span>
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
                      {!isStudent && (
                        <>
                          <td className={sc("status")}>
                            {ex.sync_status === "ready" ? (
                              // Synced = a green circled check; anything not yet
                              // synced stays a muted dash, and the in-between /
                              // failure states keep their diagnostic pills.
                              <span className="sync-ok" title="Synced" aria-label="Synced">
                                <IconCheckCircle size={16} />
                              </span>
                            ) : ex.sync_status === "never_synced" ? (
                              <span className="cell-muted">—</span>
                            ) : (
                              <span className={`sync-status ${ex.sync_status}`}>
                                {ex.sync_status.replace(/_/g, " ")}
                              </span>
                            )}{" "}
                            {ex.archived && <span className="sync-status archived">archived</span>}{" "}
                            {ex.missing_from_image && (
                              <span className="sync-status config_error">missing from image</span>
                            )}{" "}
                            {jobs[ex.slug] && <StatusPill job={jobs[ex.slug]} kind="sync" />}
                          </td>
                          <td className={`${sc("synced")} cell-muted`}>
                            {ex.last_synced_at ?? "—"}
                          </td>
                        </>
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
                    {isStudent
                      ? "Exercises will appear here once your mentor publishes them."
                      : "Authored exercise folders ship in the backend image."}
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
          title={
            syncConfirm === "all"
              ? "Sync All Exercises"
              : syncConfirm.length === 1
                ? "Sync Exercise"
                : "Sync Exercises"
          }
          confirmLabel={
            syncConfirm === "all"
              ? "Sync all"
              : syncConfirm.length === 1
                ? "Sync"
                : `Sync ${syncConfirm.length} exercises`
          }
          confirmClassName="btn primary"
          busyLabel="Starting…"
          onConfirm={async () => {
            const target = syncConfirm;
            setSyncConfirm(null);
            if (target === "all") void startSync(undefined);
            // Each exercise gets its own background job (and per-slug lock),
            // so the selected ones sync in parallel.
            else if (target) target.forEach((ex) => void startSync(ex.slug));
          }}
          onClose={() => setSyncConfirm(null)}
        >
          {syncConfirm === "all" ? (
            <p>
              Get <strong>all active exercises</strong> ready for grading? This
              uses each exercise&rsquo;s current files. It can take a while and
              runs in the background.
            </p>
          ) : syncConfirm.length === 1 ? (
            <p>
              Get <strong>{syncConfirm[0].title ?? syncConfirm[0].slug}</strong>{" "}
              ready for grading? This uses its current files and runs in the
              background.
            </p>
          ) : (
            <>
              <p>
                Get these <strong>{syncConfirm.length} exercises</strong> ready
                for grading? This uses each one&rsquo;s current files and runs
                in the background.
              </p>
              <ul className="bulk-list">
                {syncConfirm.map((ex) => (
                  <li key={ex.slug}>{ex.title ?? ex.slug}</li>
                ))}
              </ul>
            </>
          )}
        </ConfirmModal>
      )}
      {archiveTarget && archiveTarget.length > 0 && isAdmin && (
        <ConfirmModal
          title={archiveTarget.length === 1 ? "Archive Exercise" : "Archive Exercises"}
          confirmLabel={
            archiveTarget.length === 1
              ? "Archive"
              : `Archive ${archiveTarget.length} exercises`
          }
          confirmClassName="btn primary"
          busyLabel="Archiving…"
          onConfirm={() =>
            setArchivedMany(archiveTarget, true).then(() => setArchiveTarget(null))
          }
          onClose={() => setArchiveTarget(null)}
        >
          {archiveTarget.length === 1 ? (
            <p>
              Archive{" "}
              <strong>{archiveTarget[0].title ?? archiveTarget[0].slug}</strong>?
              While archived, it won&rsquo;t be graded or counted toward student
              totals.
            </p>
          ) : (
            <>
              <p>
                Archive these <strong>{archiveTarget.length} exercises</strong>?
                While archived, they won&rsquo;t be graded or counted toward
                student totals.
              </p>
              <ul className="bulk-list">
                {archiveTarget.map((ex) => (
                  <li key={ex.slug}>{ex.title ?? ex.slug}</li>
                ))}
              </ul>
            </>
          )}
          <p className="hint">
            Nothing is deleted — you can bring {archiveTarget.length === 1 ? "it" : "them"} back anytime.
          </p>
        </ConfirmModal>
      )}
      {deleting && deleting.length > 0 && isAdmin && (
        <ConfirmModal
          title={deleting.length === 1 ? "Delete Exercise" : "Delete Exercises"}
          confirmLabel={
            deleting.length === 1
              ? `Delete ${deleting[0].title ?? deleting[0].slug}`
              : `Delete ${deleting.length} exercises`
          }
          onConfirm={() => deleteExercises(deleting)}
          onClose={() => setDeleting(null)}
        >
          {deleting.length === 1 ? (
            <p>
              Permanently delete{" "}
              <strong>{deleting[0].title ?? deleting[0].slug}</strong>? This
              removes its description, input files, and grades, and clears its
              result from every student&rsquo;s report (points and totals
              update automatically). To keep it without grading it, use Archive
              instead.
            </p>
          ) : (
            <>
              <p>
                Permanently delete these{" "}
                <strong>{deleting.length} exercises</strong>? This removes
                their descriptions, input files, and grades, and clears their
                results from every student&rsquo;s report (points and totals
                update automatically). To keep them without grading them, use
                Archive instead.
              </p>
              <ul className="bulk-list">
                {deleting.map((ex) => (
                  <li key={ex.slug}>{ex.title ?? ex.slug}</li>
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
