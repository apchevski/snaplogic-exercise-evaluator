// Thin fetch wrapper. Every call carries the Cognito ID token (the API
// Gateway JWT authorizer validates it; backend/src/api.py reads the email
// and cognito:groups claims from it).

import type {
  AppConfig,
  CreateExercisePayload,
  CreateExerciseResult,
  DeleteExerciseSummary,
  DeleteStudentSummary,
  Difference,
  Exercise,
  ExerciseAnalytics,
  ExerciseDetail,
  Job,
  Report,
  ReportEdit,
  ReportVersion,
  StudentMeta,
  UpdateExercisePayload,
  UpdateStudentPayload,
  UpdateUserSettingsPayload,
  UserSettings,
} from "./types";

const API_URL: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// A 401 from any authenticated call means the Cognito session is dead — the id
// token expired and could not be silently renewed (its refresh token lives 12h;
// see infra/modules/cognito-auth). App.tsx registers a handler here that clears
// the session so the UI drops back to the login screen, instead of every page
// rendering a dead-end "Unauthorized" banner. 403 is deliberately excluded: it
// means the signed-in user's role can't do this action, not that the session is
// gone (e.g. a student hitting an admin-only route stays logged in).
let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function request<T>(
  token: string,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  if (!resp.ok) {
    if (resp.status === 401) unauthorizedHandler?.();
    let message = text || resp.statusText;
    try {
      message = JSON.parse(text).message ?? message;
    } catch {
      /* plain-text error body */
    }
    throw new ApiError(resp.status, message);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

/** Map a grading scope onto the POST /v1/gradings body: single slug →
 * 'task', several → 'tasks', empty/undefined → full grading. */
function gradingScope(tasks?: string | string[]): { task?: string; tasks?: string[] } {
  if (typeof tasks === "string") return tasks ? { task: tasks } : {};
  if (!tasks || tasks.length === 0) return {};
  return tasks.length === 1 ? { task: tasks[0] } : { tasks };
}

export const api = {
  // Non-secret SnapLogic settings (default student project space etc.).
  getConfig: (token: string) =>
    request<{ config: AppConfig }>(token, "GET", "/v1/config"),

  // The caller's own credentials (masked) + judge model (admin/mentor).
  getSettings: (token: string) =>
    request<{ settings: UserSettings }>(token, "GET", "/v1/settings"),

  // Partial update of the caller's own credentials/model; only keys present
  // are applied, null/"" clears. SnapLogic credentials are admin-only.
  updateSettings: (token: string, payload: UpdateUserSettingsPayload) =>
    request<{ settings: UserSettings }>(token, "PUT", "/v1/settings", payload),

  listStudents: (token: string) =>
    request<{ students: StudentMeta[] }>(token, "GET", "/v1/students"),

  getStudent: (token: string, slug: string) =>
    request<{ student: StudentMeta; report: Report | null }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}`,
    ),

  // Edit a graded report in place — no re-grade, no AI cost. Either the
  // report's overall summary, or one task's summary / deductions (differences)
  // / bonus answer / points. Editing differences recomputes that task's points
  // unless a manual override is in force; `points` (int) pins the score,
  // `points: null` clears the override. Returns the same shape as getStudent.
  updateStudentReport: (
    token: string,
    slug: string,
    payload: {
      overall_summary?: string;
      task?: string;
      summary?: string;
      differences?: Difference[];
      bonus_question_answer?: string | null;
      points?: number | null;
    },
  ) =>
    request<{ student: StudentMeta; report: Report }>(
      token,
      "PATCH",
      `/v1/students/${encodeURIComponent(slug)}/report`,
      payload,
    ),

  // Immutable audit log of every manual edit to this student's report
  // (admin/mentor only; newest first). Powers the "Edit history" panel.
  getReportEdits: (token: string, slug: string) =>
    request<{ edits: ReportEdit[] }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}/report/edits`,
    ),

  // The report-history index: one row per grading run, newest first. Powers
  // the version picker on the student detail page.
  listStudentReports: (token: string, slug: string) =>
    request<{ reports: ReportVersion[] }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}/reports`,
    ),

  // One historical report version's full report.json (immutable S3 copy).
  getStudentReportVersion: (token: string, slug: string, version: string) =>
    request<{ version: string; meta: StudentMeta; report: Report | null }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}/reports/${encodeURIComponent(version)}`,
    ),

  // Recent grade/sync jobs, newest first. Powers the Activity Logs page.
  // Admins get every user's jobs; mentors get only their own (backend-scoped).
  listJobs: (token: string) =>
    request<{ jobs: Job[] }>(token, "GET", "/v1/jobs"),

  // Jobs still queued/running/batch-processing anywhere in the deployment
  // (admin/mentor, never scoped to the caller). Lets the UI show what is
  // grading right now and disable a second grading for the same student —
  // regardless of who started it or which browser session it came from.
  listActiveJobs: (token: string) =>
    request<{ jobs: Job[] }>(token, "GET", "/v1/jobs/active"),

  // Per-exercise aggregates across every student's current report
  // (admin/mentor). Powers the Analytics panel.
  getExerciseAnalytics: (token: string) =>
    request<{ students_reported: number; exercises: ExerciseAnalytics[] }>(
      token,
      "GET",
      "/v1/analytics/exercises",
    ),

  listExercises: (token: string) =>
    request<{ exercises: Exercise[] }>(token, "GET", "/v1/exercises"),

  // Returns a short-lived presigned S3 URL; the browser downloads directly
  // from S3 (files can exceed what a Lambda response can carry).
  getExerciseResourceUrl: (token: string, slug: string, filename: string) =>
    request<{ filename: string; url: string; expires_in: number }>(
      token,
      "GET",
      `/v1/exercises/${encodeURIComponent(slug)}/resources/${encodeURIComponent(filename)}`,
    ),

  // Admin only. Returns presigned S3 PUT URLs for the declared input files;
  // upload them with uploadToPresignedUrl afterwards.
  createExercise: (token: string, payload: CreateExercisePayload) =>
    request<CreateExerciseResult>(token, "POST", "/v1/exercises", payload),

  // Full authored content (description/notes/config) — powers the edit dialog.
  getExercise: (token: string, slug: string) =>
    request<{ exercise: ExerciseDetail }>(
      token,
      "GET",
      `/v1/exercises/${encodeURIComponent(slug)}`,
    ),

  // Admin only. Partial update; also returns presigned PUT URLs for any
  // newly declared input files.
  updateExercise: (token: string, slug: string, payload: UpdateExercisePayload) =>
    request<CreateExerciseResult>(
      token,
      "PUT",
      `/v1/exercises/${encodeURIComponent(slug)}`,
      payload,
    ),

  // Admin only. Permanently removes the student everywhere: card, report
  // history, job rows, and every stored report file in S3 (all versions).
  deleteStudent: (token: string, slug: string) =>
    request<{ deleted: DeleteStudentSummary }>(
      token,
      "DELETE",
      `/v1/students/${encodeURIComponent(slug)}`,
    ),

  // Admin only. Permanently removes the exercise everywhere: S3 content and
  // artifacts (all versions), DynamoDB row, sync-job rows — and scrubs its
  // result out of every student's live report.
  deleteExercise: (token: string, slug: string) =>
    request<{ deleted: DeleteExerciseSummary }>(
      token,
      "DELETE",
      `/v1/exercises/${encodeURIComponent(slug)}`,
    ),

  // Add a student to the list without grading anything. The optional
  // project space and project name are stored on the student and dictate
  // where every later grading run looks for their pipelines. An email
  // additionally creates a read-only web login for the student (Cognito
  // sends them a temporary password).
  registerStudent: (
    token: string,
    student: string,
    space?: string,
    project?: string,
    email?: string,
  ) =>
    request<{ student: StudentMeta }>(token, "POST", "/v1/students", {
      student,
      ...(space ? { space } : {}),
      ...(project ? { project } : {}),
      ...(email ? { email } : {}),
    }),

  // Admin only. Partial update of a registered student — only the keys sent
  // are applied. The slug never changes (it keys the report history and the
  // stored reports), so a renamed student keeps their grades. `project: null`
  // restores the display-name default; `email: null` removes the student's
  // login, a different address replaces it.
  updateStudent: (token: string, slug: string, payload: UpdateStudentPayload) =>
    request<{ student: StudentMeta }>(
      token,
      "PUT",
      `/v1/students/${encodeURIComponent(slug)}`,
      payload,
    ),

  // No tasks = full grading; a string or a subset of slugs only (re)grades
  // those exercises. Every run also refreshes the AI Overall summary.
  // `regrade` records intent only (the task card's Regrade button was clicked)
  // so the grading history can label the run — it changes nothing about how
  // the run executes. `slug` addresses the student card directly: a renamed
  // student keeps their original slug, so deriving it from the name would
  // grade into a new, empty card. Always pass it when the caller has it.
  startGrading: (
    token: string,
    student: string,
    tasks?: string | string[],
    opts?: { regrade?: boolean; slug?: string },
  ) =>
    request<{ id: string }>(token, "POST", "/v1/gradings", {
      student,
      ...(opts?.slug ? { slug: opts.slug } : {}),
      ...gradingScope(tasks),
      ...(opts?.regrade ? { regrade: true } : {}),
    }),

  getGrading: (token: string, id: string) =>
    request<Job>(token, "GET", `/v1/gradings/${encodeURIComponent(id)}`),

  startSync: (token: string, slug?: string) =>
    request<{ id: string }>(token, "POST", "/v1/syncs", slug ? { slug } : {}),

  getSync: (token: string, id: string) =>
    request<Job>(token, "GET", `/v1/syncs/${encodeURIComponent(id)}`),
};

/** Upload one file straight to S3 — the presigned URL carries the auth,
 * so no Authorization header (and no API URL prefix) here. */
export async function uploadToPresignedUrl(url: string, file: File): Promise<void> {
  const resp = await fetch(url, { method: "PUT", body: file });
  if (!resp.ok) {
    throw new ApiError(resp.status, `Uploading ${file.name} failed (${resp.statusText}).`);
  }
}

interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  /** On timeout: "throw" a 408 (default), or "stop" and return the last job.
   * A full "grade all" batch runs in the background and can outlast a browser
   * poll, so it stops quietly — the report shows up on the next refresh. */
  onTimeout?: "throw" | "stop";
}

/** Poll a job until it reaches a terminal state (or the timeout policy fires). */
export async function pollJob(
  fetchJob: () => Promise<Job>,
  onUpdate: (job: Job) => void,
  { intervalMs = 3000, timeoutMs = 20 * 60 * 1000, onTimeout = "throw" }: PollOptions = {},
): Promise<Job> {
  const deadline = Date.now() + timeoutMs;
  let last: Job | null = null;
  for (;;) {
    const job = await fetchJob();
    last = job;
    onUpdate(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    if (Date.now() > deadline) {
      if (onTimeout === "stop") return last;
      throw new ApiError(408, "Timed out waiting for the job to finish.");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
