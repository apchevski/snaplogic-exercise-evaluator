import { useState } from "react";

interface Props {
  /** Default student project space (from GET /v1/config); prefills the field. */
  defaultSpace: string;
  /** Registers the student; throws (rejects) on failure so the dialog can
   * stay open and show the error. */
  onSubmit: (
    name: string,
    space?: string,
    project?: string,
    email?: string,
  ) => Promise<void>;
  onClose: () => void;
}

/** Registration dialog: student name plus the SnapLogic project space and
 * project the grader should look in. What's saved here dictates where every
 * later grading run searches for this student's pipelines. An optional email
 * additionally creates a read-only web login for the student. */
export function AddStudentModal({ defaultSpace, onSubmit, onClose }: Props) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [space, setSpace] = useState(defaultSpace);
  const [project, setProject] = useState("");
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
          <h2>Add Student</h2>
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
              Add an email to give this student their own login. They&rsquo;ll
              get a temporary password by email; once they sign in they can view
              their grades, but can&rsquo;t change anything.
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
            Adding a student doesn&rsquo;t grade anything yet. We just check that
            their SnapLogic project exists and add them to the dashboard, ready
            to grade whenever you like.
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
            {busy ? "Adding…" : "Add Student"}
          </button>
        </footer>
      </form>
    </div>
  );
}
