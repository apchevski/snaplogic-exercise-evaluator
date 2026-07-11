// Inline editor for a single task's whole evaluation: the summary, the
// deductions/notes list (differences), the bonus-question answer, and the
// points. Saving PATCHes the stored report in place — no re-grade. Editing
// deductions recomputes the task's points server-side (points = 10 − Σ
// deductions), which the editor previews live; typing a Points value instead
// pins a manual override (10 − Σ bypassed) that a mentor can reset.
import { useState } from "react";

import { isAiJudged } from "../types";
import type { Difference, TaskResult } from "../types";
import { IconCheck, IconClose, IconPlus, IconTrash } from "./icons";

const MAX_POINTS = 10;

export interface TaskEvaluationPayload {
  summary?: string;
  differences?: Difference[];
  bonus_question_answer?: string | null;
  // int → pin an override; null → clear an existing override; omitted → leave
  // points to the server (recompute from deductions, or untouched).
  points?: number | null;
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
  // MISSING, NEEDS-SYNC, or procedural-FAIL card gets the summary + a direct
  // points override only (its 10 − Σ score doesn't apply — matches the backend
  // _task_is_ai_judged guard, which still lets points be overridden).
  const adjustable = isAiJudged(task);
  const [summary, setSummary] = useState(task.summary ?? task.reason ?? "");
  const [diffs, setDiffs] = useState<Difference[]>(
    (task.differences ?? []).map((d) => ({ ...d })),
  );
  const [bonus, setBonus] = useState(task.bonus_question_answer ?? "");

  // Points: a manual override "sticks"; otherwise the field tracks the
  // deduction-derived value. `wasManual` is the server's current state, so we
  // know whether a save must clear an existing override (points: null).
  const wasManual = !!task.points_manual;
  const [pointsManual, setPointsManual] = useState<boolean>(wasManual);
  const [pointsValue, setPointsValue] = useState<number>(
    typeof task.points === "number" ? task.points : 0,
  );

  const totalDeducted = diffs.reduce(
    (s, d) => s + (Number(d.points_deducted) || 0),
    0,
  );
  const computedPoints = Math.max(0, MAX_POINTS - totalDeducted);
  // Value shown in the points input: the pinned value when manual, else the
  // computed 10 − Σ (adjustable) or the task's current score (non-adjustable).
  const displayedPoints = pointsManual
    ? pointsValue
    : adjustable
      ? computedPoints
      : typeof task.points === "number"
        ? task.points
        : 0;

  const setPointsFromInput = (raw: string) => {
    setPointsManual(true);
    setPointsValue(
      raw === ""
        ? 0
        : Math.max(0, Math.min(MAX_POINTS, Number.parseInt(raw, 10) || 0)),
    );
  };

  const updateDiff = (i: number, patch: Partial<Difference>) =>
    setDiffs((prev) => prev.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const removeDiff = (i: number) =>
    setDiffs((prev) => prev.filter((_, j) => j !== i));
  const addDiff = () => setDiffs((prev) => [...prev, emptyDifference()]);

  const summaryTrimmed = summary.trim();
  // Every listed deduction/note must at least describe itself.
  const diffsValid = diffs.every((d) => d.description.trim().length > 0);
  // A save must carry at least one change. Adjustable tasks always ship the
  // deductions list, so a summary + valid diffs is enough; a non-adjustable
  // task needs either a summary or a points action (set/clear an override).
  const pointsAction = pointsManual || (wasManual && !pointsManual);
  const canSave =
    !saving &&
    diffsValid &&
    (adjustable
      ? summaryTrimmed.length > 0
      : summaryTrimmed.length > 0 || pointsAction);

  const save = () => {
    if (!canSave) return;
    const payload: TaskEvaluationPayload = {};
    if (summaryTrimmed) payload.summary = summaryTrimmed;
    // Points: pin an override, clear a prior one, or leave it to the server.
    if (pointsManual) {
      payload.points = Math.max(0, Math.min(MAX_POINTS, Math.round(displayedPoints)));
    } else if (wasManual) {
      payload.points = null; // reset → fall back to the computed value
    }
    if (adjustable) {
      payload.differences = diffs.map((d) => ({
        area: d.area.trim(),
        description: d.description.trim(),
        points_deducted: Math.max(
          0,
          Math.min(MAX_POINTS, Number(d.points_deducted) || 0),
        ),
        rule_source: (d.rule_source ?? "").trim(),
        reasoning: (d.reasoning ?? "").trim(),
      }));
      // Empty clears the answer (stored as null) — the exercise may have no
      // bonus question at all.
      payload.bonus_question_answer = bonus.trim() || null;
    }
    onSave(payload);
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

      <div className="eval-field">
        <span className="eval-label">Points</span>
        <div className="points-control">
          <input
            type="number"
            min={0}
            max={MAX_POINTS}
            value={displayedPoints}
            onChange={(e) => setPointsFromInput(e.target.value)}
            disabled={saving}
          />
          <span className="eval-points-hint">/ {MAX_POINTS}</span>
          {adjustable && !pointsManual && (
            <span className="eval-points-hint">computed 10 − {totalDeducted}</span>
          )}
          {pointsManual && (
            <>
              <span className="points-manual-tag">manually adjusted</span>
              <button
                type="button"
                className="link-btn"
                onClick={() => setPointsManual(false)}
                disabled={saving}
              >
                {adjustable ? "reset to computed" : "clear override"}
              </button>
            </>
          )}
        </div>
      </div>

      {adjustable && (
        <>
          <div className="eval-section">
            <div className="eval-section-head">
              <span className="eval-label">Deductions &amp; notes</span>
              <span className="eval-points-preview">Σ deductions: −{totalDeducted}</span>
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
                      <IconTrash />
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
              <IconPlus />
              Add deduction / note
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
          <IconCheck />
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="btn small" onClick={onCancel} disabled={saving}>
          <IconClose />
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
