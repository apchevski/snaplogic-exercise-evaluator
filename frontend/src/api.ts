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
  method: "GET" | "POST" | "PUT",
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

export const api = {
  listStudents: (token: string) =>
    request<{ students: StudentMeta[] }>(token, "GET", "/v1/students"),

  getStudent: (token: string, slug: string) =>
    request<{ student: StudentMeta; report: Report | null }>(
      token,
      "GET",
      `/v1/students/${encodeURIComponent(slug)}`,
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

  startGrading: (token: string, student: string, task?: string) =>
    request<{ id: string }>(token, "POST", "/v1/gradings", {
      student,
      ...(task ? { task } : {}),
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
