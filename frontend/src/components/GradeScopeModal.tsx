import { useEffect, useState } from "react";

import type { Exercise } from "../types";

interface Props {
  studentName: string;
  /** Active (non-archived) exercises to offer; all preselected. */
  exercises: Exercise[];
  /** True when the student isn't on the list yet — offers "Register only". */
  isNew?: boolean;
  onStart: (tasks: string[] | null) => void; // null = all exercises (full run)
  onRegister?: () => void;
  onClose: () => void;
}

/** Scope picker shown before a grading starts: grade every exercise
 * (default — also refreshes the AI Overall summary), grade a selected
 * subset, or just register the student without grading anything yet. */
export function GradeScopeModal({
  studentName,
  exercises,
  isNew,
  onStart,
  onRegister,
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
                  {e.prep_status !== "ready" && (
                    <span
                      className="warn-chip"
                      title="Not prepped — grading will skip it until it's prepped on the Exercises page."
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
            <p className="hint">
              Grading all exercises also refreshes the AI overall summary; a
              subset only updates those exercises&rsquo; results.
            </p>
          </div>
        </div>
        <footer>
          {isNew && onRegister && (
            <button type="button" className="btn" onClick={onRegister} style={{ marginRight: "auto" }}>
              Register only (no grading)
            </button>
          )}
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
