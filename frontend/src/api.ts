// Thin fetch wrapper. Every call carries the Cognito ID token (the API
// Gateway JWT authorizer validates it; backend/src/api.py reads the email
// and cognito:groups claims from it).

import type {
  AppConfig,
  CreateExercisePayload,
  CreateExerciseResult,
  DeleteExerciseSummary,
  DeleteStudentSummary,
  Exercise,
  ExerciseDetail,
  Job,
  Report,
  StudentMeta,
  UpdateExercisePayload,
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

  listStudents: (token: string) =>
    request<{ students: StudentMeta[] }>(token, "GET", "/v1/students"),

  getStudent: (token: string, slug: string) =>
    request<{ student: StudentMeta; report: Report | null }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}`,
    ),

  // Rewrite AI-written report text in place (overall summary or one task's
  // summary) — no re-grade, no AI cost. Returns the same shape as getStudent.
  updateStudentReport: (
    token: string,
    slug: string,
    payload: { overall_summary?: string; task?: string; summary?: string },
  ) =>
    request<{ student: StudentMeta; report: Report }>(
      token,
      "PATCH",
      `/v1/students/${encodeURIComponent(slug)}/report`,
      payload,
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

  // No tasks = full grading (also refreshes the AI Overall summary); a
  // string or a subset of slugs only (re)grades those exercises.
  startGrading: (token: string, student: string, tasks?: string | string[]) =>
    request<{ id: string }>(token, "POST", "/v1/gradings", {
      student,
      ...gradingScope(tasks),
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

/** Poll a job until it reaches a terminal state. */
export async function pollJob(
  fetchJob: () => Promise<Job>,
  onUpdate: (job: Job) => void,
  intervalMs = 3000,
  timeoutMs = 20 * 60 * 1000,
): Promise<Job> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await fetchJob();
    onUpdate(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    if (Date.now() > deadline) {
      throw new ApiError(408, "Timed out waiting for the job to finish.");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
