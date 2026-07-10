import { describe, expect, it } from "vitest";
import { taskProvenance } from "./types";
import type { TaskResult } from "./types";

function task(patch: Partial<TaskResult>): TaskResult {
  return { slug: "task_x", status: "evaluated", verdict: "pass", ...patch };
}

describe("taskProvenance", () => {
  it("attributes an untouched AI-judged task to the AI", () => {
    expect(taskProvenance(task({}))).toEqual({ kind: "ai" });
  });

  it("attributes an edited task to its last editor, with the timestamp", () => {
    const prov = taskProvenance(
      task({ edited_by: "mentor@x.io", edited_at: "2026-07-10T18:30:00+00:00" }),
    );
    expect(prov).toEqual({
      kind: "edited",
      by: "mentor@x.io",
      at: "2026-07-10T18:30:00+00:00",
    });
  });

  it("shows nothing for an untouched non-AI result (MISSING)", () => {
    expect(taskProvenance(task({ status: "missing", verdict: null }))).toBeNull();
  });

  it("shows nothing for an untouched procedural FAIL (name mismatch)", () => {
    expect(
      taskProvenance(
        task({ verdict: "fail", failing_gate: "pipeline_name_match" }),
      ),
    ).toBeNull();
  });

  it("still attributes an edited MISSING task to its editor (points override)", () => {
    const prov = taskProvenance(
      task({ status: "missing", verdict: null, edited_by: "admin@x.io" }),
    );
    expect(prov).toMatchObject({ kind: "edited", by: "admin@x.io" });
  });
});
