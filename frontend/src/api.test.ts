import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, onUnauthorized, pollJob } from "./api";
import type { Job } from "./types";

function job(status: Job["status"]): Job {
  return { job_id: "j1", job_type: "grade", status, target: "student" };
}

describe("ApiError", () => {
  it("carries the status code and message and is a real Error", () => {
    const err = new ApiError(404, "not found");
    expect(err).toBeInstanceOf(Error);
    expect(err.status).toBe(404);
    expect(err.message).toBe("not found");
  });
});

describe("api.getExerciseResourceUrl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("URL-encodes the slug and filename and returns the parsed body", async () => {
    const payload = { filename: "My File.zip", url: "https://s3.example/x", expires_in: 300 };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const out = await api.getExerciseResourceUrl("tok", "task_01", "My File.zip");

    expect(out).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/exercises/task_01/resources/My%20File.zip",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({ Authorization: "Bearer tok" }),
      }),
    );
  });
});

describe("api.deleteStudent / api.deleteExercise", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends DELETE to the student route and returns the purge summary", async () => {
    const payload = { deleted: { student: "jane-doe", rows: 3, jobs: 2, objects: 6 } };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const out = await api.deleteStudent("tok", "jane-doe");

    expect(out).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/students/jane-doe",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({ Authorization: "Bearer tok" }),
      }),
    );
  });

  it("sends DELETE to the exercise route and surfaces API errors", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ message: "Requires one of roles ['admin']" }), {
          status: 403,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.deleteExercise("tok", "task_01")).rejects.toMatchObject({
      status: 403,
      message: "Requires one of roles ['admin']",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/exercises/task_01",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("onUnauthorized", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    onUnauthorized(null);
  });

  it("fires the handler on a 401 and still throws the ApiError", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ message: "Unauthorized" }), { status: 401 })),
    );

    await expect(api.listStudents("expired")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not fire the handler on a 403 (role forbidden, session still valid)", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ message: "Forbidden" }), { status: 403 })),
    );

    await expect(api.listStudents("tok")).rejects.toMatchObject({ status: 403 });
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("pollJob", () => {
  it("returns immediately when the job is already terminal", async () => {
    const done = job("succeeded");
    const fetchJob = vi.fn(async () => done);
    const onUpdate = vi.fn();

    const result = await pollJob(fetchJob, onUpdate, 1, 1000);

    expect(result).toBe(done);
    expect(fetchJob).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith(done);
  });

  it("keeps polling until the job reaches a terminal state", async () => {
    let call = 0;
    const fetchJob = vi.fn(async () =>
      call++ === 0 ? job("running") : job("succeeded"),
    );
    const onUpdate = vi.fn();

    const result = await pollJob(fetchJob, onUpdate, 1, 1000);

    expect(result.status).toBe("succeeded");
    expect(fetchJob).toHaveBeenCalledTimes(2);
    expect(onUpdate).toHaveBeenCalledTimes(2);
  });

  it("throws an ApiError once the timeout is exceeded", async () => {
    const fetchJob = vi.fn(async () => job("running"));

    await expect(pollJob(fetchJob, vi.fn(), 1, -1)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
