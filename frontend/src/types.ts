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
  status: string; // evaluated | missing | needs_prep | config_error | missing_evaluation
  verdict: string | null; // pass | fail | null
  points?: number | null;
  summary?: string | null;
  reason?: string | null;
  student_pipeline_name?: string | null;
  differences?: Difference[];
  bonus_question_answer?: string | null;
  failing_gate?: string | null;
  failing_gate_detail?: string | null;
}

export interface Counts {
  pass: number;
  fail: number;
  missing: number;
  needs_prep: number;
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
  tasks: TaskResult[];
}

export interface StudentMeta {
  slug: string;
  display_name: string;
  space?: string | null;
  counts?: Counts;
  points_earned?: number;
  points_possible?: number;
  overall_summary?: string | null;
  graded_at?: string;
  latest_version?: string;
  requested_by?: string;
  report_json_key?: string;
}

export interface Job {
  job_id: string;
  job_type: "grade" | "prep";
  status: "queued" | "running" | "succeeded" | "failed";
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
  prep_status: string;
  reason?: string;
  last_prepped_at?: string;
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
 * "auto": prep derives everything for a single-output file-writer. */
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
  prep_status?: string;
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
