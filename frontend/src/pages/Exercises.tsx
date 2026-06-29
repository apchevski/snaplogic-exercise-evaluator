import { useCallback, useEffect, useState } from "react";

import { api, pollJob } from "../api";
import { useIsAdmin, useToken } from "../auth";
import { StatusPill } from "../components/StatusPill";
import type { Exercise, Job } from "../types";

export default function Exercises() {
  const token = useToken();
  const isAdmin = useIsAdmin();
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { exercises } = await api.listExercises(token);
      setExercises(exercises);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const startPrep = useCallback(
    async (slug?: string) => {
      const key = slug ?? "__all__";
      setError(null);
      try {
        const { id } = await api.startPrep(token, slug);
        const job = await pollJob(
          () => api.getPrep(token, id),
          (j) => setJobs((prev) => ({ ...prev, [key]: j })),
        );
        if (job.status === "succeeded") void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [token, refresh],
  );

  const anyBusy = Object.values(jobs).some(
    (j) => j.status === "queued" || j.status === "running",
  );

  return (
    <main>
      {error && <div className="error-banner">{error}</div>}
      {isAdmin && (
        <div className="total-row" style={{ marginBottom: "1rem" }}>
          <button
            className="btn primary"
            onClick={() => void startPrep()}
            disabled={anyBusy}
          >
            Prep all exercises
          </button>
          {jobs["__all__"] && <StatusPill job={jobs["__all__"]} kind="prep" />}
        </div>
      )}
      {exercises.map((ex) => (
        <div className="exercise-row" key={ex.slug}>
          <h3>
            {ex.title ?? ex.slug}
            <span className="slug">{ex.slug}</span>
          </h3>
          <span className={`prep-status ${ex.prep_status}`}>
            {ex.prep_status.replace(/_/g, " ")}
          </span>
          {ex.task_type && <span className="job-cost">{ex.task_type}</span>}
          {ex.last_prepped_at && (
            <span className="job-cost">prepped {ex.last_prepped_at}</span>
          )}
          {ex.missing_from_image && (
            <span className="prep-status config_error">missing from image</span>
          )}
          {isAdmin && (
            <>
              <button
                className="btn"
                onClick={() => void startPrep(ex.slug)}
                disabled={anyBusy}
              >
                Prep
              </button>
              {jobs[ex.slug] && <StatusPill job={jobs[ex.slug]} kind="prep" />}
            </>
          )}
        </div>
      ))}
      {!loading && exercises.length === 0 && (
        <div className="empty-state">
          <h2>No exercises found</h2>
          <p>Authored exercise folders ship in the backend image.</p>
        </div>
      )}
    </main>
  );
}
