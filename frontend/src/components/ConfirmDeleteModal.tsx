import { useState, type ReactNode } from "react";

interface Props {
  title: string;
  /** Body copy spelling out exactly what the delete will destroy. */
  children: ReactNode;
  /** Red confirm button label, e.g. `Delete "Jane Doe"`. */
  confirmLabel: string;
  /** Performs the delete; throws (rejects) on failure so the dialog can
   * stay open and show the error. The parent closes it on success. */
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

/** Confirmation dialog guarding a permanent delete. Nothing happens until
 * the red button is clicked; any error keeps the dialog open. */
export function ConfirmDeleteModal({
  title,
  children,
  confirmLabel,
  onConfirm,
  onClose,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div className="modal modal-narrow" role="alertdialog" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}
          {children}
        </div>
        <footer>
          {busy && <span className="modal-busy">Deleting…</span>}
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => void confirm()}
            disabled={busy}
          >
            {confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}
