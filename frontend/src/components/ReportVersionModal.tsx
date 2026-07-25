import type { Report } from "../types";
import { IconClose } from "./icons";

/** Read-only overlay showing one historical report version exactly as it was
 * graded — the Overall paragraph plus a compact per-task list. Opened from the
 * Grading-history panel on the student detail page; the live report and every
 * edit/regrade action stay untouched. */
export function ReportVersionModal({
  version,
  report,
  onClose,
}: {
  version: string;
  report: Report | null;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal report-version-modal"
        role="dialog"
        aria-label="Historical report"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>
            Report from {report?.graded_at ?? version}
            <span className="hint"> (historical — read only)</span>
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <IconClose />
          </button>
        </header>
        <div className="modal-body">
          {!report ? (
            <p className="summary muted">This version has no stored report.</p>
          ) : (
            <>
              <div className="badges">
                <span className="badge pass">{report.counts?.pass ?? 0} pass</span>
                <span className="badge fail">{report.counts?.fail ?? 0} fail</span>
                <span className="badge missing">{report.counts?.missing ?? 0} missing</span>
                <span className="badge">
                  {report.points_earned}/{report.points_possible} pts
                </span>
              </div>
              {report.overall_summary && <p className="overall">{report.overall_summary}</p>}
              <ul className="version-task-list">
                {(report.tasks ?? []).map((t) => (
                  <li key={t.slug} className="version-task">
                    <span className={`verdict-tag ${t.verdict ?? t.status}`}>
                      {t.verdict ?? t.status}
                    </span>
                    <span className="cell-mono">{t.slug}</span>
                    <span className="version-task-pts">
                      {typeof t.points === "number" ? `${t.points}/10` : "—"}
                    </span>
                    {t.summary && <p className="summary">{t.summary}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
