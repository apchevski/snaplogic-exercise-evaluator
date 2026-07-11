import type { ReactNode } from "react";

/** Informational dialog with a single OK button — for messages that need
 * acknowledgement but offer no action to confirm (e.g. "only one student can
 * be graded at a time"). */
export function InfoModal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal modal-narrow" role="alertdialog" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">{children}</div>
        <footer>
          <button type="button" className="btn primary" onClick={onClose}>
            OK
          </button>
        </footer>
      </div>
    </div>
  );
}
