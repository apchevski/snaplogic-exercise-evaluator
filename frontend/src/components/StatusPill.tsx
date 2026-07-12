import type { Job } from "../types";
import { IconCheckCircle, IconXCircle } from "./icons";

const LABELS: Record<Job["status"], string> = {
  queued: "Queued",
  running: "Grading…",
  batch_processing: "Batch grading…",
  succeeded: "Completed",
  failed: "Failed",
};

export function StatusPill({ job, kind }: { job: Job; kind: "grade" | "sync" }) {
  const busy =
    job.status === "queued" ||
    job.status === "running" ||
    job.status === "batch_processing";
  // Sync progress is icon-only: just a spinner while queued/running, then the
  // same green circled check the Sync Status column shows for a synced row —
  // or a red circled ✕ whose hover tooltip carries the error.
  if (kind === "sync") {
    if (busy) {
      return (
        <span className="sync-busy" title="Syncing…" aria-label="Syncing…">
          <span className="spinner" />
        </span>
      );
    }
    if (job.status === "failed") {
      return (
        <span
          className="sync-fail"
          title={job.error ?? "Sync failed"}
          aria-label={`Sync failed: ${job.error ?? "unknown error"}`}
        >
          <IconXCircle size={16} />
        </span>
      );
    }
    return (
      <span className="sync-ok" title="Synced" aria-label="Synced">
        <IconCheckCircle size={16} />
      </span>
    );
  }
  const cost = job.result?.usage?.est_cost_usd;
  return (
    <span>
      <span className={`status-pill ${job.status}`}>
        {busy && <span className="spinner" />}
        {LABELS[job.status]}
      </span>
      {job.status === "succeeded" && typeof cost === "number" && (
        <span className="job-cost"> ≈ ${cost.toFixed(2)}</span>
      )}
      {job.status === "failed" && job.error && (
        <div className="job-error">{job.error}</div>
      )}
    </span>
  );
}
