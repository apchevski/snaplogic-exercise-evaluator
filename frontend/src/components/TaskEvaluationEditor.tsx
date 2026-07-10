// Inline editor for a single task's whole evaluation: the summary, the
// deductions/notes list (differences), and the bonus-question answer. Saving
// PATCHes the stored report in place — no re-grade. Editing deductions
// recomputes the task's points server-side (points = 10 − Σ deductions), so
// the editor shows a live preview of that same arithmetic.
import { useState } from "react";

import { isAiJudged } from "../types";
import type { Difference, TaskResult } from "../types";

const MAX_POINTS = 10;

export interface TaskEvaluationPayload {
  summary?: string;
  differences?: Difference[];
  bonus_question_answer?: string | null;
}

function emptyDifference(): Difference {
  return {
    area: "",
    description: "",
    points_deducted: 0,
    rule_source: "",
    reasoning: "",
  };
}

export function TaskEvaluationEditor({
  task,
  saving,
  error,
  onSave,
  onCancel,
}: {
  task: TaskResult;
  saving: boolean;
  error: string | null;
  onSave: (payload: TaskEvaluationPayload) => void;
  onCancel: () => void;
}) {
  // Only an AI-judged exercise has editable deductions / a bonus answer; a
  // MISSING, NEEDS-SYNC, or procedural-FAIL card just gets the summary field
  // (its score is fixed — matches the backend _task_is_ai_judged guard).
  const adjustable = isAiJudged(task);
  const [summary, setSummary] = useState(task.summary ?? task.reason ?? "");
  const [diffs, setDiffs] = useState<Difference[]>(
    (task.differences ?? []).map((d) => ({ ...d })),
  );
  const [bonus, setBonus] = useState(task.bonus_question_answer ?? "");

  const totalDeducted = diffs.reduce(
    (s, d) => s + (Number(d.points_deducted) || 0),
    0,
  );
  const previewPoints = Math.max(0, MAX_POINTS - totalDeducted);

  const updateDiff = (i: number, patch: Partial<Difference>) =>
    setDiffs((prev) => prev.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const removeDiff = (i: number) =>
    setDiffs((prev) => prev.filter((_, j) => j !== i));
  const addDiff = () => setDiffs((prev) => [...prev, emptyDifference()]);

  const summaryTrimmed = summary.trim();
  // Every listed deduction/note must at least describe itself.
  const diffsValid = diffs.every((d) => d.description.trim().length > 0);
  const canSave = summaryTrimmed.length > 0 && diffsValid && !saving;

  const save = () => {
    if (!canSave) return;
    if (!adjustable) {
      onSave({ summary: summaryTrimmed });
      return;
    }
    onSave({
      summary: summaryTrimmed,
      differences: diffs.map((d) => ({
        area: d.area.trim(),
        description: d.description.trim(),
        points_deducted: Math.max(
          0,
          Math.min(MAX_POINTS, Number(d.points_deducted) || 0),
        ),
        rule_source: (d.rule_source ?? "").trim(),
        reasoning: (d.reasoning ?? "").trim(),
      })),
      // Empty clears the answer (stored as null) — the exercise may have no
      // bonus question at all.
      bonus_question_answer: bonus.trim() || null,
    });
  };

  return (
    <div className="eval-editor">
      <label className="eval-field">
        <span className="eval-label">Summary</span>
        <textarea
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          rows={3}
          disabled={saving}
          autoFocus
        />
      </label>

      {adjustable && (
        <>
          <div className="eval-section">
            <div className="eval-section-head">
              <span className="eval-label">Deductions &amp; notes</span>
              <span className="eval-points-preview">
                Points: {previewPoints}/{MAX_POINTS}
                <span className="eval-points-hint"> (10 − {totalDeducted})</span>
              </span>
            </div>

            {diffs.length === 0 && (
              <p className="eval-empty">
                No deductions or notes — this exercise scores full points.
              </p>
            )}

            {diffs.map((d, i) => {
              const cost = Number(d.points_deducted) || 0;
              return (
                <div className="eval-diff" key={i}>
                  <div className="eval-diff-row">
                    <label className="eval-field grow">
                      <span className="eval-sublabel">Area</span>
                      <input
                        type="text"
                        value={d.area}
                        onChange={(e) => updateDiff(i, { area: e.target.value })}
                        disabled={saving}
                        placeholder="e.g. Mapper configuration"
                      />
                    </label>
                    <label className="eval-field points">
                      <span className="eval-sublabel">−Points</span>
                      <input
                        type="number"
                        min={0}
                        max={MAX_POINTS}
                        value={d.points_deducted}
                        onChange={(e) =>
                          updateDiff(i, {
                            points_deducted: e.target.value
                              ? Math.max(
                                  0,
                                  Math.min(
                                    MAX_POINTS,
                                    Number.parseInt(e.target.value, 10) || 0,
                                  ),
                                )
                              : 0,
                          })
                        }
                        disabled={saving}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn small danger"
                      onClick={() => removeDiff(i)}
                      disabled={saving}
                      title="Remove this item"
                    >
                      Remove
                    </button>
                  </div>
                  <label className="eval-field">
                    <span className="eval-sublabel">Description</span>
                    <textarea
                      value={d.description}
                      onChange={(e) =>
                        updateDiff(i, { description: e.target.value })
                      }
                      rows={2}
                      disabled={saving}
                      placeholder="What is wrong / different"
                    />
                  </label>
                  <div className="eval-diff-row">
                    <label className="eval-field grow">
                      <span className="eval-sublabel">Rule source</span>
                      <input
                        type="text"
                        value={d.rule_source ?? ""}
                        onChange={(e) =>
                          updateDiff(i, { rule_source: e.target.value })
                        }
                        disabled={saving}
                        placeholder="e.g. general_rules: filter before sort"
                      />
                    </label>
                    <label className="eval-field grow">
                      <span className="eval-sublabel">Reasoning</span>
                      <input
                        type="text"
                        value={d.reasoning ?? ""}
                        onChange={(e) =>
                          updateDiff(i, { reasoning: e.target.value })
                        }
                        disabled={saving}
                        placeholder="Why the rule applies"
                      />
                    </label>
                  </div>
                  <p className="eval-diff-kind">
                    {cost > 0 ? `Deduction (−${cost})` : "Note (no deduction)"}
                  </p>
                </div>
              );
            })}

            <button
              type="button"
              className="btn small"
              onClick={addDiff}
              disabled={saving}
            >
              + Add deduction / note
            </button>
          </div>

          <label className="eval-field">
            <span className="eval-label">Bonus question evaluation</span>
            <textarea
              value={bonus}
              onChange={(e) => setBonus(e.target.value)}
              rows={2}
              disabled={saving}
              placeholder="Leave empty if this exercise has no bonus question."
            />
          </label>
        </>
      )}

      <div className="editor-actions">
        <button
          className="btn small primary"
          onClick={save}
          disabled={!canSave}
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="btn small" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
      {!diffsValid && (
        <div className="job-error">Every deduction / note needs a description.</div>
      )}
      {error && <div className="job-error">{error}</div>}
    </div>
  );
}
