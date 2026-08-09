import { useState } from "react";

import type { StudentMeta } from "../types";

interface Props {
  /** Default student project space (from GET /v1/config); prefills the field
   * when registering. Ignored in edit mode — the card's own space wins. */
  defaultSpace: string;
  /** The student being edited; omit to register a new one. */
  initial?: StudentMeta | null;
  /** Registers or saves the student; throws (rejects) on failure so the dialog
   * can stay open and show the error. In edit mode `name` is the new display
   * name, and empty `project`/`email` mean "clear it". */
  onSubmit: (
    name: string,
    space?: string,
    project?: string,
    email?: string,
  ) => Promise<void>;
  onClose: () => void;
}

/** Student dialog, both modes: the student name plus the SnapLogic project
 * space and project the grader should look in. What's saved here dictates
 * where every later grading run searches for this student's pipelines. An
 * optional email additionally gives the student a read-only web login.
 *
 * Editing takes the same fields as registering. The student's slug — the
 * identity behind their grades, report history and detail-page URL — is not
 * one of them: it's fixed at registration, so a rename relabels the student
 * and keeps everything they've been graded on. */
export function StudentModal({ defaultSpace, initial, onSubmit, onClose }: Props) {
  const isEdit = !!initial;
  const [name, setName] = useState(initial?.display_name ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [space, setSpace] = useState(initial?.space ?? defaultSpace);
  const [project, setProject] = useState(initial?.project ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const student = name.trim();
    if (!student || !space.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(
        student,
        space.trim() || undefined,
        project.trim() || undefined,
        email.trim() || undefined,
      );
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <form
        className="modal modal-narrow"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <header>
          <h2>
            {isEdit
              ? `Edit Student — ${initial?.display_name ?? name}`
              : "Add Student"}
          </h2>
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
          {error && <div className="error-banner">{error}</div>}
          <div className="modal-field">
            <label>
              Student name<span className="req-star">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Jane Doe"
              autoFocus
            />
            {isEdit && (
              <p className="hint">
                Renaming keeps everything this student has been graded on —
                their grades, history and page all stay put.
              </p>
            )}
          </div>
          <div className="modal-field">
            <label>Student email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="e.g. jane.doe@example.com"
            />
            <p className="hint">
              {isEdit ? (
                <>
                  Add an email to give this student their own login, or clear
                  it to take the login away. A different address replaces the
                  old login, and the student gets a new temporary password by
                  email.
                </>
              ) : (
                <>
                  Add an email to give this student their own login. They&rsquo;ll
                  get a temporary password by email; once they sign in they can
                  view their grades, but can&rsquo;t change anything.
                </>
              )}
            </p>
          </div>
          <div className="modal-field">
            <label>
              Project space<span className="req-star">*</span>
            </label>
            <input
              type="text"
              value={space}
              onChange={(e) => setSpace(e.target.value)}
              placeholder={defaultSpace || "e.g. Training_Program_Demo"}
            />
            <p className="hint">
              The SnapLogic project space where this student&rsquo;s work is
              saved.
            </p>
          </div>
          <div className="modal-field">
            <label>Project</label>
            <input
              type="text"
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder={name.trim() || "Defaults to the student name"}
            />
            <p className="hint">
              Leave this empty if the project has the same name as the student
              — that&rsquo;s the usual case.
            </p>
          </div>
          <p className="hint">
            {isEdit ? (
              <>
                Saving doesn&rsquo;t regrade anything. We just check that the
                SnapLogic project still exists where you&rsquo;ve pointed it and
                update the student — the next grading run looks there.
              </>
            ) : (
              <>
                Adding a student doesn&rsquo;t grade anything yet. We just check
                that their SnapLogic project exists and add them to the
                dashboard, ready to grade whenever you like.
              </>
            )}
          </p>
        </div>
        <footer>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={!name.trim() || !space.trim() || busy}
          >
            {isEdit
              ? busy
                ? "Saving…"
                : "Save changes"
              : busy
                ? "Adding…"
                : "Add Student"}
          </button>
        </footer>
      </form>
    </div>
  );
}
