// Mirrors the backend contracts: report.json (evaluator/grade.py), the
// DynamoDB item shapes (backend/src/common.py docstring), and the API
// responses (backend/src/api.py).

export interface Difference {
  area: string;
  description: string;
  points_deducted: number;
  rule_source?: string;
  reasoning?: string;
}

export interface TaskResult {
  slug: string;
  status: string; // evaluated | missing | needs_sync | config_error | missing_evaluation
  verdict: string | null; // pass | fail | null
  points?: number | null;
  // True when a mentor/admin pinned the points directly (10 − Σ bypassed).
  points_manual?: boolean;
  summary?: string | null;
  reason?: string | null;
  student_pipeline_name?: string | null;
  differences?: Difference[];
  bonus_question_answer?: string | null;
  failing_gate?: string | null;
  failing_gate_detail?: string | null;
  // Provenance: set once a mentor/admin edits this task's evaluation. Absent
  // means the evaluation is exactly as the AI (or hard gate) produced it.
  edited_by?: string | null;
  edited_at?: string | null;
  summary_edited_by?: string | null;
  summary_edited_at?: string | null;
}

// Hard-gate failures that still route to the AI judge for partial credit
// (points = 10 − Σ deductions). Mirrors backend _OUTPUT_MISMATCH_GATES.
const OUTPUT_MISMATCH_GATES = new Set(["output_match", "triggered_task_responses_match"]);

/** True when the task's score came from the AI judge, so its deductions and
 * bonus answer can be edited (points recompute as 10 − Σ deductions). False for
 * MISSING / NEEDS-SYNC tasks and procedural FAILs (e.g. name mismatch), whose
 * score is fixed and whose empty deduction list must not be recomputed. */
export function isAiJudged(task: TaskResult): boolean {
  if (task.status !== "evaluated") return false;
  if (
    task.verdict === "fail" &&
    task.failing_gate &&
    !OUTPUT_MISMATCH_GATES.has(task.failing_gate)
  )
    return false;
  return true;
}

export type TaskProvenance =
  | { kind: "ai" }
  | { kind: "edited"; by: string; at?: string | null }
  | null;

/** Who last touched a task's evaluation, for the card's provenance line:
 * `edited` once a mentor/admin changed it; `ai` for an untouched AI-judged
 * result; null for an untouched non-AI result (a MISSING / name-mismatch card
 * has nothing meaningful to attribute). */
export function taskProvenance(task: TaskResult): TaskProvenance {
  if (task.edited_by) return { kind: "edited", by: task.edited_by, at: task.edited_at };
  return isAiJudged(task) ? { kind: "ai" } : null;
}

export interface Counts {
  pass: number;
  fail: number;
  missing: number;
  needs_sync?: number;
  // Pre-rename reports (before prep→sync) carry this instead of needs_sync;
  // read `needs_sync ?? needs_prep` when displaying historical grades.
  needs_prep?: number;
  total?: number;
}

export interface Report {
  student: string;
  project_space?: string;
  student_project_path?: string;
  graded_at?: string;
  counts: Counts;
  points_earned: number;
  points_possible: number;
  overall_summary?: string | null;
  overall_summary_edited_by?: string | null;
  overall_summary_edited_at?: string | null;
  tasks: TaskResult[];
}

/** One immutable audit-log row (GET /v1/students/{slug}/report/edits). */
export interface ReportEditChange {
  field: string; // summary | deductions | bonus | points | overall_summary
  from?: string | number | null;
  to?: string | number | null;
}

export interface ReportEdit {
  edited_by: string;
  edited_at: string;
  target: string; // "overall" | "task:<slug>"
  changes: ReportEditChange[];
}

export interface StudentMeta {
  slug: string;
  display_name: string;
  space?: string | null;
  // SnapLogic project holding the student's pipelines; unset/null means
  // the project is named exactly after the student.
  project?: string | null;
  // Full org/space/project path, computed server-side so the detail view can
  // show it even for students who've never been graded (no report yet).
  student_project_path?: string | null;
  counts?: Counts;
  points_earned?: number;
  points_possible?: number;
  overall_summary?: string | null;
  graded_at?: string;
  latest_version?: string;
  requested_by?: string;
  report_json_key?: string;
  // Set when the student was added via "register without grading".
  registered_by?: string;
  registered_at?: string;
  // Set when registration also created a read-only web login for the student.
  email?: string | null;
  // Stamped on the card whenever a mentor/admin edits the stored report.
  report_edited_by?: string | null;
  report_edited_at?: string | null;
}

/** Non-secret SnapLogic settings from GET /v1/config (prefills the Add
 * Student dialog's project space). */
export interface AppConfig {
  org_name?: string | null;
  student_project_space?: string | null;
  solution_project_space?: string | null;
  solution_project?: string | null;
}

/** One selectable AI judge model (GET /v1/settings `allowed_models`). */
export interface JudgeModelOption {
  id: string;
  label: string;
  // Short cost/capability blurb shown next to the label in the picker.
  description: string;
}

/** The caller's own credentials + judge model from GET/PUT /v1/settings.
 * Secrets never come back — only whether one is stored (plus a short tail
 * of the API key so the owner can tell which key it is). */
export interface UserSettings {
  email: string;
  // Personal SnapLogic login (admins only; grading/sync jobs the user starts
  // run under it — otherwise the shared credentials apply).
  snaplogic_username?: string | null;
  snaplogic_password_set: boolean;
  // Personal Anthropic API key for grading (admin or mentor).
  anthropic_api_key_set: boolean;
  anthropic_api_key_hint?: string | null;
  // Judge model for gradings this user starts; null = project default.
  judge_model?: string | null;
  default_model: string;
  allowed_models: JudgeModelOption[];
  updated_at?: string | null;
}

/** PUT /v1/settings body: only keys present are applied; null/"" clears. */
export interface UpdateUserSettingsPayload {
  snaplogic_username?: string | null;
  snaplogic_password?: string | null;
  anthropic_api_key?: string | null;
  judge_model?: string | null;
}

export interface Job {
  job_id: string;
  job_type: "grade" | "sync";
  // "batch_processing": a full "grade all" run is judging every exercise via
  // the (asynchronous, 50%-cheaper) Message Batches API — the worker is
  // polling the batch to completion in the background.
  status: "queued" | "running" | "batch_processing" | "succeeded" | "failed";
  target: string;
  error?: string;
  requested_by?: string;
  created_at?: string;
  updated_at?: string;
  result?: {
    version?: string;
    counts?: Counts;
    points_earned?: number;
    points_possible?: number;
    usage?: { est_cost_usd?: number; calls?: number };
    exercises?: { slug: string; status: string }[];
  };
}

export interface ExerciseResource {
  filename: string;
  size_bytes: number;
}

export interface Exercise {
  slug: string;
  title?: string;
  description?: string | null;
  task_type?: string | null;
  sync_status: string;
  reason?: string;
  last_synced_at?: string;
  max_points?: number;
  missing_from_image?: boolean;
  archived?: boolean;
  resources?: ExerciseResource[];
}

export interface TriggeredRequest {
  name: string;
  params: Record<string, string>;
}

/** Structured replacement for the hand-written task.json. Absent/null =
 * "auto": sync derives everything for a single-output file-writer. */
export type TaskConfig =
  | {
      task_type: "file_writer";
      output_filenames: string[];
      output_match_mode?: "exact" | "columns_only";
    }
  | {
      task_type: "triggered_task";
      triggered_task_name: string;
      requests: TriggeredRequest[];
    };

export interface ExerciseDetail {
  slug: string;
  title?: string;
  description_md?: string | null;
  notes_md?: string | null;
  task_config?: TaskConfig | null;
  resources?: ExerciseResource[];
  archived?: boolean;
  sync_status?: string;
}

export interface CreateExercisePayload {
  slug: string;
  description_md: string;
  notes_md?: string;
  task_config?: TaskConfig;
  resources?: { filename: string }[];
}

export interface UpdateExercisePayload {
  description_md?: string;
  notes_md?: string;
  task_config?: TaskConfig | null;
  resources?: { filename: string }[];
  remove_resources?: string[];
  archived?: boolean;
}

export interface ExerciseUpload {
  filename: string;
  url: string; // presigned S3 PUT, browser uploads directly
  expires_in: number;
}

export interface CreateExerciseResult {
  exercise: Exercise;
  uploads: ExerciseUpload[];
}

/** DELETE /v1/students/{slug} — what the purge removed. */
export interface DeleteStudentSummary {
  student: string;
  rows: number; // DynamoDB rows (card + report history)
  jobs: number; // grade-job rows
  objects: number; // S3 object versions
}

/** DELETE /v1/exercises/{slug} — what the purge removed. */
export interface DeleteExerciseSummary {
  exercise: string;
  objects: number; // S3 object versions
  jobs: number; // sync-job rows
  reports_scrubbed: number; // student reports the result was removed from
  tombstoned: boolean; // folder still ships in the image; marker row kept
}
