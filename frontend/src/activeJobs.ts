// Deployment-wide "what is grading right now" state, polled from the backend.
//
// Job progress used to live only in React state, so it vanished on a browser
// refresh and was invisible to everyone else — two mentors (or one mentor in
// two tabs) could each start a grading for the same student and only find out
// from a 409. `GET /v1/jobs/active` reads the persisted JOB rows instead, so
// the answer is the same in every session: which students are being graded,
// by whom, and for how long.
//
// The backend's per-target LOCK row is still the real guard (a duplicate POST
// 409s regardless). This is what lets the UI *show* that, and disable the
// button, instead of failing on click.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { Job } from "./types";

/** Poll interval. The endpoint scans the (TTL-bounded) JOB rows, so this is
 * deliberately not sub-second; grading runs take minutes, not milliseconds.
 * Polling pauses entirely while the tab is hidden. */
const POLL_MS = 10_000;

export interface ActiveGradings {
  /** Grade jobs still queued / running / batch-processing, newest first —
   * every user's, not just the caller's. */
  jobs: Job[];
  /** The in-flight grade job for a student slug, if any. */
  forStudent: (slug: string) => Job | undefined;
  /** Poll again immediately (e.g. right after starting a job). */
  refresh: () => void;
}

/** Poll the in-flight grade jobs. `enabled` is false for roles that can't
 * grade — the endpoint 403s them and they have no button to guard. */
export function useActiveGradings(token: string, enabled = true): ActiveGradings {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled || !token) {
      setJobs([]);
      return;
    }
    let alive = true;
    const load = () => {
      // Nothing on a hidden tab: the list is re-read the moment it's shown
      // again (visibilitychange below), so there is no stale window to see.
      if (document.visibilityState === "hidden") return;
      api
        .listActiveJobs(token)
        .then(({ jobs }) => {
          if (alive) setJobs(jobs.filter((j) => j.job_type === "grade"));
        })
        // A failed poll keeps the last known list rather than blanking the
        // panel or raising a page banner — the next tick corrects it.
        .catch(() => {});
    };
    load();
    const id = window.setInterval(load, POLL_MS);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", load);
    };
  }, [token, enabled, tick]);

  const forStudent = useCallback(
    // Grade jobs are keyed on the student slug (`_create_job("grade", slug…)`).
    (slug: string) => jobs.find((j) => j.target === slug),
    [jobs],
  );

  return { jobs, forStudent, refresh };
}

/** Fire `onFinished` when a job that was in flight stops being in flight —
 * whoever started it. That's the cue to re-read the grades it just rewrote,
 * so a page nobody touched still catches up on its own. */
export function useOnGradingFinished(jobs: Job[], onFinished: () => void): void {
  const previous = useRef<string[] | null>(null);
  useEffect(() => {
    const ids = jobs.map((j) => j.job_id);
    const before = previous.current;
    previous.current = ids;
    // First poll establishes the baseline — it never counts as a completion.
    if (before && before.some((id) => !ids.includes(id))) onFinished();
  }, [jobs, onFinished]);
}

/** "4m" / "1h 12m" since an ISO timestamp — how long a run has been going.
 * There is no honest estimate of time *remaining* (a batch is Anthropic's to
 * schedule), so elapsed is what we show. */
export function elapsedSince(iso?: string): string | null {
  if (!iso) return null;
  const started = new Date(iso).getTime();
  if (Number.isNaN(started)) return null;
  const secs = Math.max(0, Math.round((Date.now() - started) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}
