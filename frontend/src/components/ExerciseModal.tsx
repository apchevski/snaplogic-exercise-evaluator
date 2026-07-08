import { useState, type FormEvent } from "react";

import { api, uploadToPresignedUrl } from "../api";
import type { ExerciseDetail, ExerciseResource, TaskConfig } from "../types";

/** "Task 07 – Router Basics" → "task_07_router_basics" — the stable exercise id
 * we derive from the name, so nobody has to type a folder slug by hand. */
function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // strip diacritics left by NFKD
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

/** Split authored markdown into its H1 (the exercise / pipeline name) and the
 * remaining body. S3 still stores one description.md with the name as its H1
 * on top; the dialog just shows the two parts as separate fields. */
function splitH1(markdown: string): { name: string; body: string } {
  const md = markdown ?? "";
  const lines = md.split("\n");
  const i = lines.findIndex((l) => {
    const t = l.trim();
    return t.startsWith("# ") && !t.startsWith("## ");
  });
  if (i === -1) return { name: "", body: md.trim() };
  const name = lines[i].trim().slice(2).trim();
  const body = [...lines.slice(0, i), ...lines.slice(i + 1)].join("\n").trim();
  return { name, body };
}

const SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SCENARIO_NAME_RE = /^[a-z0-9][a-z0-9_]{0,63}$/;

/** "mathOperation=3+5; round=true" → { mathOperation: "3+5", round: "true" } */
function parseParams(text: string): Record<string, string> {
  const params: Record<string, string> = {};
  for (const chunk of text.split(/[;\n]/)) {
    const piece = chunk.trim();
    if (!piece) continue;
    const eq = piece.indexOf("=");
    if (eq <= 0) throw new Error(`Parameter "${piece}" is not in key=value form.`);
    params[piece.slice(0, eq).trim()] = piece.slice(eq + 1).trim();
  }
  return params;
}

function formatParams(params: Record<string, string>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

type TaskTypeChoice = "auto" | "file_writer" | "triggered_task";

interface ScenarioRow {
  name: string;
  params: string;
}

interface Props {
  token: string;
  /** Present = edit mode; absent = create mode. */
  initial?: ExerciseDetail | null;
  onClose: () => void;
  /** Called after a successful save (and after a partial failure, so the
   * table refreshes to whatever state actually stuck). */
  onSaved: () => void;
}

/** Create/edit dialog for an exercise. Authored markdown + input files go to
 * S3 (files browser → S3 via presigned PUTs); the task-type config is stored
 * as structured data and the worker generates task.json from it at sync time
 * — nobody hand-writes JSON. */
export function ExerciseModal({ token, initial, onClose, onSaved }: Props) {
  const isEdit = !!initial;
  const cfg = initial?.task_config ?? null;

  const parsed = splitH1(initial?.description_md ?? "");
  const [exerciseName, setExerciseName] = useState(parsed.name);
  const [description, setDescription] = useState(parsed.body);
  const [notes, setNotes] = useState(initial?.notes_md ?? "");
  const [taskType, setTaskType] = useState<TaskTypeChoice>(cfg?.task_type ?? "auto");
  const [outputFilenames, setOutputFilenames] = useState(
    cfg?.task_type === "file_writer" ? cfg.output_filenames.join(", ") : "",
  );
  const [matchMode, setMatchMode] = useState<"exact" | "columns_only">(
    (cfg?.task_type === "file_writer" && cfg.output_match_mode) || "exact",
  );
  const [triggeredName, setTriggeredName] = useState(
    cfg?.task_type === "triggered_task" ? cfg.triggered_task_name : "",
  );
  const [scenarios, setScenarios] = useState<ScenarioRow[]>(
    cfg?.task_type === "triggered_task"
      ? cfg.requests.map((r) => ({ name: r.name, params: formatParams(r.params) }))
      : [{ name: "", params: "" }],
  );
  const existing: ExerciseResource[] = initial?.resources ?? [];
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onTaskTypeChange = (t: TaskTypeChoice) => {
    setTaskType(t);
    if (t === "triggered_task" && !triggeredName) {
      const name = exerciseName.trim();
      if (name) setTriggeredName(`${name} Task`);
    }
  };

  const setScenario = (i: number, patch: Partial<ScenarioRow>) =>
    setScenarios((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const buildTaskConfig = (): TaskConfig | null => {
    if (taskType === "auto") return null;
    if (taskType === "file_writer") {
      const names = outputFilenames
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (names.length === 0) {
        throw new Error("List at least one output filename (comma-separated).");
      }
      return {
        task_type: "file_writer",
        output_filenames: names,
        ...(matchMode !== "exact" ? { output_match_mode: matchMode } : {}),
      };
    }
    const name = triggeredName.trim();
    if (!name) throw new Error("The Triggered Task name is required.");
    const requests = scenarios
      .filter((s) => s.name.trim() || s.params.trim())
      .map((s) => {
        const scenarioName = s.name.trim();
        if (!SCENARIO_NAME_RE.test(scenarioName)) {
          throw new Error(
            `Scenario name "${scenarioName}" must be lowercase letters, digits and '_' (it becomes a filename).`,
          );
        }
        return { name: scenarioName, params: parseParams(s.params) };
      });
    if (requests.length === 0) throw new Error("Add at least one request scenario.");
    return { task_type: "triggered_task", triggered_task_name: name, requests };
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const name = exerciseName.trim();
    if (!name) {
      setError("Exercise name is required.");
      return;
    }
    // The slug is a stable id derived from the name (never edited after create).
    const slug = isEdit ? initial!.slug : slugify(name);
    if (!SLUG_RE.test(slug)) {
      setError("Give the exercise a name with some letters or numbers in it.");
      return;
    }
    // S3 keeps a single description.md with the name as its H1 on top; the
    // dialog just splits that into two fields for editing.
    const body = description.trim();
    const descriptionMd = body ? `# ${name}\n\n${body}\n` : `# ${name}\n`;
    let taskConfig: TaskConfig | null;
    try {
      taskConfig = buildTaskConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    try {
      setBusy(isEdit ? "Saving changes…" : "Creating exercise…");
      const newResources = files.length
        ? { resources: files.map((f) => ({ filename: f.name })) }
        : {};
      const { uploads } = isEdit
        ? await api.updateExercise(token, slug, {
            description_md: descriptionMd,
            notes_md: notes,
            task_config: taskConfig, // null clears back to auto
            ...newResources,
            ...(removed.size ? { remove_resources: [...removed] } : {}),
          })
        : await api.createExercise(token, {
            slug,
            description_md: descriptionMd,
            ...(notes.trim() ? { notes_md: notes } : {}),
            ...(taskConfig ? { task_config: taskConfig } : {}),
            ...newResources,
          });
      for (const upload of uploads) {
        const file = files.find((f) => f.name === upload.filename);
        if (!file) continue;
        setBusy(`Uploading ${upload.filename}…`);
        await uploadToPresignedUrl(upload.url, file);
      }
      onSaved();
      onClose();
    } catch (err) {
      // The save may have partially landed (e.g. content saved, one upload
      // failed) — refresh the table either way so it shows what stuck.
      onSaved();
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <form className="modal" onSubmit={(e) => void submit(e)}>
        <header>
          <h2>
            {isEdit
              ? `Edit Exercise — ${initial?.title ?? exerciseName ?? initial?.slug}`
              : "Add New Exercise"}
          </h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={!!busy}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          <div className="modal-field">
            <label htmlFor="ex-name">
              Exercise Name<span className="req-star">*</span>
            </label>
            <input
              id="ex-name"
              type="text"
              value={exerciseName}
              onChange={(e) => setExerciseName(e.target.value)}
              placeholder="Task 07 – Router Basics"
              required
            />
            <div className="hint">
              This is the pipeline name — sync looks the solution pipeline up by it.
            </div>
          </div>

          <div className="modal-field">
            <label htmlFor="ex-description">
              Description<span className="req-star">*</span>
            </label>
            <textarea
              id="ex-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={"### Objective:\n\nDescribe what the student must build…"}
              required
            />
            <div className="hint">What the student must build. Markdown supported.</div>
          </div>

          <div className="modal-field">
            <label htmlFor="ex-notes">AI Guidance</label>
            <textarea
              id="ex-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Instructor hints for the AI judge (task-specific rules, deductions, edge cases)."
            />
          </div>

          <div className="modal-field">
            <label htmlFor="ex-type">Task type</label>
            <select
              id="ex-type"
              value={taskType}
              onChange={(e) => onTaskTypeChange(e.target.value as TaskTypeChoice)}
            >
              <option value="auto">File writer — single output (auto-detected by sync)</option>
              <option value="file_writer">File writer — multiple / custom outputs</option>
              <option value="triggered_task">Triggered task (HTTP scenarios)</option>
            </select>
            <div className="hint">
              Sync generates the task config from this — no task.json to hand-write.
            </div>
          </div>

          {taskType === "file_writer" && (
            <>
              <div className="modal-field">
                <label htmlFor="ex-outputs">
                  Output filenames<span className="req-star">*</span> (comma-separated)
                </label>
                <input
                  id="ex-outputs"
                  type="text"
                  value={outputFilenames}
                  onChange={(e) => setOutputFilenames(e.target.value)}
                  placeholder="Report1.csv, Report2.csv, Report3.csv"
                  spellCheck={false}
                />
              </div>
              <div className="modal-field">
                <label htmlFor="ex-match">Output comparison</label>
                <select
                  id="ex-match"
                  value={matchMode}
                  onChange={(e) => setMatchMode(e.target.value as "exact" | "columns_only")}
                >
                  <option value="exact">Exact — columns + rows must match</option>
                  <option value="columns_only">
                    Columns only — for non-deterministic outputs
                  </option>
                </select>
              </div>
            </>
          )}

          {taskType === "triggered_task" && (
            <>
              <div className="modal-field">
                <label htmlFor="ex-ttname">
                  Triggered Task name<span className="req-star">*</span>
                </label>
                <input
                  id="ex-ttname"
                  type="text"
                  value={triggeredName}
                  onChange={(e) => setTriggeredName(e.target.value)}
                  placeholder="Task 07 – Router Basics Task"
                  spellCheck={false}
                />
                <div className="hint">
                  Convention: the pipeline name + " Task" (matching is strict).
                </div>
              </div>
              <div className="modal-field">
                <label>
                  Request scenarios<span className="req-star">*</span>
                </label>
                {scenarios.map((s, i) => (
                  <div className="scenario-row" key={i}>
                    <input
                      type="text"
                      value={s.name}
                      onChange={(e) => setScenario(i, { name: e.target.value })}
                      placeholder="scenario_name"
                      spellCheck={false}
                      aria-label={`Scenario ${i + 1} name`}
                    />
                    <input
                      type="text"
                      value={s.params}
                      onChange={(e) => setScenario(i, { params: e.target.value })}
                      placeholder="param=value; other=value"
                      spellCheck={false}
                      aria-label={`Scenario ${i + 1} params`}
                    />
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => setScenarios((prev) => prev.filter((_, j) => j !== i))}
                      disabled={scenarios.length === 1}
                      aria-label={`Remove scenario ${i + 1}`}
                    >
                      ✕
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn small"
                  onClick={() => setScenarios((prev) => [...prev, { name: "", params: "" }])}
                >
                  + Add scenario
                </button>
                <div className="hint">
                  Each scenario invokes the student's Triggered Task with those query
                  parameters; the name becomes the expected-response filename.
                </div>
              </div>
            </>
          )}

          <div className="modal-field">
            <label htmlFor="ex-files">Input files</label>
            {existing.length > 0 && (
              <span className="resource-list" style={{ marginBottom: 6 }}>
                {existing.map((r) => {
                  const marked = removed.has(r.filename);
                  return (
                    <button
                      key={r.filename}
                      type="button"
                      className={`resource-chip${marked ? " chip-removed" : ""}`}
                      onClick={() =>
                        setRemoved((prev) => {
                          const next = new Set(prev);
                          if (next.has(r.filename)) next.delete(r.filename);
                          else next.add(r.filename);
                          return next;
                        })
                      }
                      title={
                        marked
                          ? `${r.filename} will be deleted on save — click to keep`
                          : `Click to delete ${r.filename} on save`
                      }
                    >
                      {marked ? "↩" : "✕"} {r.filename}
                    </button>
                  );
                })}
              </span>
            )}
            <input
              id="ex-files"
              type="file"
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            {files.length > 0 && (
              <span className="resource-list" style={{ marginTop: 6 }}>
                {files.map((f) => (
                  <button
                    key={f.name}
                    type="button"
                    className="resource-chip"
                    onClick={() => setFiles((prev) => prev.filter((x) => x.name !== f.name))}
                    title={`Remove ${f.name}`}
                  >
                    ✕ {f.name}
                  </button>
                ))}
              </span>
            )}
            <div className="hint">
              Stored in the exercise's resources/ folder for students to download.
              {existing.length > 0 && " Click an existing file to mark it for deletion."}
            </div>
          </div>
        </div>
        <footer>
          {busy && <span className="modal-busy">{busy}</span>}
          <button type="button" className="btn" onClick={onClose} disabled={!!busy}>
            Cancel
          </button>
          <button type="submit" className="btn primary" disabled={!!busy}>
            {isEdit ? "Save Changes" : "Save Exercise"}
          </button>
        </footer>
      </form>
    </div>
  );
}
