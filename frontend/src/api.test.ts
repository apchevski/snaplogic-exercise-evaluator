import { describe, expect, it, vi } from "vitest";
import { ApiError, pollJob } from "./api";
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
