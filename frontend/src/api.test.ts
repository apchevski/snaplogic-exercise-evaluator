import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, pollJob } from "./api";
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
