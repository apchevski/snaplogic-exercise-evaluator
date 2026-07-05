// Thin fetch wrapper. Every call carries the Cognito ID token (the API
// Gateway JWT authorizer validates it; backend/src/api.py reads the email
// and cognito:groups claims from it).

import type {
  CreateExercisePayload,
  CreateExerciseResult,
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

async function request<T>(
  token: string,
  method: "GET" | "POST" | "PUT" | "PATCH",
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

  // Add a student to the list without grading anything.
  registerStudent: (token: string, student: string, space?: string) =>
    request<{ student: StudentMeta }>(token, "POST", "/v1/students", {
      student,
      ...(space ? { space } : {}),
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

  startPrep: (token: string, slug?: string) =>
    request<{ id: string }>(token, "POST", "/v1/preps", slug ? { slug } : {}),

  getPrep: (token: string, id: string) =>
    request<Job>(token, "GET", `/v1/preps/${encodeURIComponent(id)}`),
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
