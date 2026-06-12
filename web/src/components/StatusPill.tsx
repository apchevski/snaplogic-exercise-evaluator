import type { Job } from "../types";

const LABELS: Record<Job["status"], string> = {
  queued: "Queued",
  running: "Grading…",
  succeeded: "Done",
  failed: "Failed",
};

export function StatusPill({ job, kind }: { job: Job; kind: "grade" | "prep" }) {
  const busy = job.status === "queued" || job.status === "running";
  const label =
    job.status === "running" && kind === "prep" ? "Prepping…" : LABELS[job.status];
  const cost = job.result?.usage?.est_cost_usd;
  return (
    <span>
      <span className={`status-pill ${job.status}`}>
        {busy && <span className="spinner" />}
        {label}
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
