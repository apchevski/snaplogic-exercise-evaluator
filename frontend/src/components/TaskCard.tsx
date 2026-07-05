// Port of renderTask() from the static dashboard (evaluator/ui.py).
import type { ReactNode } from "react";

import type { Difference, TaskResult } from "../types";

const MAX_POINTS = 10;

export function tierForRatio(num: number, den: number): string {
  if (!den) return "none";
  const r = num / den;
  if (r >= 0.8) return "high";
  if (r >= 0.5) return "mid";
  return "low";
}

function DiffItem({ d }: { d: Difference }) {
  const cost = Number(d.points_deducted || 0);
  return (
    <li>
      {cost > 0 && (
        <span className="cost-chip">
          −{cost} pt{cost === 1 ? "" : "s"}
        </span>
      )}
      {d.area || "(unspecified)"}
      {d.description ? ` — ${d.description}` : ""}
      {d.reasoning ? ` — ${d.reasoning}` : ""}
      {d.rule_source && <span className="rule-source">rule: {d.rule_source}</span>}
    </li>
  );
}

export function TaskCard({
  task,
  action,
  summaryEditor,
}: {
  task: TaskResult;
  action?: ReactNode;
  /** When set, rendered in place of the summary paragraph (inline editing). */
  summaryEditor?: ReactNode;
}) {
  const verdict = task.verdict || task.status || "unknown";
  const pts = typeof task.points === "number" ? task.points : null;
  const tier = pts === null ? "none" : tierForRatio(pts, MAX_POINTS);
  const diffs = task.differences ?? [];
  const deductions = diffs.filter((d) => Number(d.points_deducted || 0) > 0);
  const notes = diffs.filter((d) => Number(d.points_deducted || 0) === 0);
  const totalCost = deductions.reduce((s, d) => s + Number(d.points_deducted || 0), 0);

  return (
    <div className={`task v-${verdict}`}>
      <header>
        <span className={`verdict-badge ${verdict}`}>{verdict.replace(/_/g, " ")}</span>
        <h3>{task.slug}</h3>
        <span className={`points-pill tier-${tier}`}>
          {pts === null ? "—" : pts}/{MAX_POINTS}
        </span>
        {action}
      </header>
      {task.student_pipeline_name && (
        <p className="task-pipeline">Pipeline: {task.student_pipeline_name}</p>
      )}
      {summaryEditor ??
        ((task.summary || task.reason) && (
          <p className="summary">{task.summary || task.reason}</p>
        ))}
      {task.failing_gate && (
        <>
          <p className="failing-gate">Failing gate: {task.failing_gate}</p>
          {task.failing_gate_detail && (
            <pre className="failing-gate">{task.failing_gate_detail}</pre>
          )}
        </>
      )}
      {deductions.length > 0 && (
        <>
          <div className="section-label">
            Deductions<span className="total-cost">(−{totalCost})</span>
          </div>
          <ul className="diff-list">
            {deductions.map((d, i) => (
              <DiffItem key={i} d={d} />
            ))}
          </ul>
        </>
      )}
      {notes.length > 0 && (
        <>
          <div className="section-label">Notes (no deduction)</div>
          <ul className="diff-list">
            {notes.map((d, i) => (
              <DiffItem key={i} d={d} />
            ))}
          </ul>
        </>
      )}
      {task.bonus_question_answer && (
        <p className="bonus">Bonus: {task.bonus_question_answer}</p>
      )}
    </div>
  );
}
