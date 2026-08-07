import { afterEach, describe, expect, it, vi } from "vitest";

import { elapsedSince } from "./activeJobs";

describe("elapsedSince", () => {
  afterEach(() => vi.useRealTimers());

  function at(now: string) {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(now));
  }

  it("returns null for a missing or unparseable timestamp", () => {
    expect(elapsedSince(undefined)).toBeNull();
    expect(elapsedSince("not a date")).toBeNull();
  });

  it("counts seconds under a minute", () => {
    at("2026-08-07T12:00:42+00:00");
    expect(elapsedSince("2026-08-07T12:00:00+00:00")).toBe("42s");
  });

  it("counts whole minutes under an hour", () => {
    at("2026-08-07T12:07:30+00:00");
    expect(elapsedSince("2026-08-07T12:00:00+00:00")).toBe("7m");
  });

  it("splits hours and minutes past an hour", () => {
    at("2026-08-07T13:12:00+00:00");
    expect(elapsedSince("2026-08-07T12:00:00+00:00")).toBe("1h 12m");
  });

  it("clamps a future timestamp to zero rather than showing negative time", () => {
    at("2026-08-07T12:00:00+00:00");
    expect(elapsedSince("2026-08-07T12:00:30+00:00")).toBe("0s");
  });
});
