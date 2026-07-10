import { useEffect, useState } from "react";

import type { Exercise } from "../types";

interface Props {
  studentName: string;
  /** Active (non-archived) exercises to offer; all preselected. */
  exercises: Exercise[];
  onStart: (tasks: string[] | null) => void; // null = all exercises (full run)
  onClose: () => void;
}

/** Scope picker shown before a grading starts: grade every exercise
 * (default — also refreshes the AI Overall summary) or grade a selected
 * subset. Registering a new student lives on the dashboard toolbar. */
export function GradeScopeModal({
  studentName,
  exercises,
  onStart,
  onClose,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(exercises.map((e) => e.slug)),
  );

  // The exercise list can arrive after the dialog opens — re-select all.
  useEffect(() => {
    setSelected(new Set(exercises.map((e) => e.slug)));
  }, [exercises]);

  const toggle = (slug: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

  const allSelected = selected.size === exercises.length;
  const toggleAll = () =>
    setSelected(
      allSelected ? new Set() : new Set(exercises.map((e) => e.slug)),
    );
  const start = () => onStart(allSelected ? null : [...selected]);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal modal-narrow">
        <header>
          <h2>Grade {studentName}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="modal-field">
            <label>Exercises to grade</label>
            <div className="check-list">
              {exercises.length > 0 && (
                <label className="check-item check-item-all">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = selected.size > 0 && !allSelected;
                    }}
                    onChange={toggleAll}
                    aria-label="Select all exercises"
                  />
                  <span>Select all</span>
                </label>
              )}
              {exercises.map((e) => (
                <label key={e.slug} className="check-item">
                  <input
                    type="checkbox"
                    checked={selected.has(e.slug)}
                    onChange={() => toggle(e.slug)}
                  />
                  <span className="cell-mono">{e.slug}</span>
                  {e.sync_status !== "ready" && (
                    <span
                      className="warn-chip"
                      title="Not synced — grading will skip it until it's synced on the Exercises page."
                    >
                      ⚠
                    </span>
                  )}
                </label>
              ))}
              {exercises.length === 0 && (
                <p className="hint">
                  Exercise list unavailable — grading will cover every active
                  exercise.
                </p>
              )}
            </div>
            {allSelected ? (
              <div className="info-note">
                <strong>Grading every exercise runs as a batch</strong> to cut
                the AI cost by about half. Because of that, results are{" "}
                <strong>not instant</strong> — usually a few minutes, and
                occasionally up to an hour. You can close this dialog and leave
                the page; the student&rsquo;s report (and the AI overall summary)
                updates automatically when the batch finishes.
              </div>
            ) : (
              <p className="hint">
                The selected exercise{selected.size === 1 ? "" : "s"} are graded{" "}
                <strong>right away</strong> at normal cost; only their results
                are updated. Grade <em>all</em> exercises to run the cheaper
                background batch and refresh the overall summary.
              </p>
            )}
          </div>
        </div>
        <footer>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={start}
            disabled={exercises.length > 0 && selected.size === 0}
          >
            {allSelected
              ? `Grade all${exercises.length > 0 ? ` ${exercises.length}` : ""} exercises`
              : `Grade ${selected.size} selected`}
          </button>
        </footer>
      </div>
    </div>
  );
}
