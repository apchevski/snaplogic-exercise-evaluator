import { useEffect, useMemo, useState } from "react";
import { useAuth } from "react-oidc-context";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useCanGrade, useToken } from "../auth";
import { Panel, SearchBox } from "../components/table";
import { tierForRatio } from "../components/TaskCard";
import type { StudentMeta } from "../types";

interface RankedStudent extends StudentMeta {
  /** Competition ranking: equal points share a rank, the next rank skips
   * (1, 2, 2, 4). Ties can therefore produce two golds and no silver. */
  rank: number;
}

/** Rank the graded students by total points. Ranks are assigned over the FULL
 * roster before any search filtering, so hiding rows never renumbers anyone. */
function rankStudents(students: StudentMeta[]): {
  ranked: RankedStudent[];
  ungraded: StudentMeta[];
} {
  const graded = students.filter((s) => s.graded_at);
  const ungraded = students
    .filter((s) => !s.graded_at)
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
  const sorted = [...graded].sort(
    (a, b) =>
      (b.points_earned ?? 0) - (a.points_earned ?? 0) ||
      a.display_name.localeCompare(b.display_name),
  );
  let rank = 0;
  let prevPts = Number.NaN;
  const ranked = sorted.map((s, i) => {
    const pts = s.points_earned ?? 0;
    if (pts !== prevPts) {
      rank = i + 1;
      prevPts = pts;
    }
    return { ...s, rank };
  });
  return { ranked, ungraded };
}

function RankBadge({ rank }: { rank: number }) {
  const medal = rank <= 3 ? ` medal-${rank}` : "";
  return (
    <span className={`board-rank${medal}`} aria-label={`Rank ${rank}`}>
      {rank}
    </span>
  );
}

export default function Ranking() {
  const auth = useAuth();
  const token = useToken();
  const canGrade = useCanGrade();
  // Same visibility rule as the Students table: staff open anyone, a student
  // only their own row (theirs is the only one still carrying an email).
  const myEmail = (auth.user?.profile?.email ?? "").trim().toLowerCase();
  const isMe = (s: StudentMeta) =>
    !canGrade && (s.email ?? "").trim().toLowerCase() === myEmail;
  const canOpen = (s: StudentMeta) => canGrade || isMe(s);

  const [students, setStudents] = useState<StudentMeta[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listStudents(token)
      .then(({ students }) => {
        if (cancelled) return;
        setStudents(students);
        setLastUpdated(new Date().toLocaleTimeString());
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const { ranked, ungraded } = useMemo(() => rankStudents(students), [students]);

  const matches = (s: StudentMeta) => {
    const q = search.trim().toLowerCase();
    return (
      !q ||
      s.display_name.toLowerCase().includes(q) ||
      (s.space ?? "").toLowerCase().includes(q) ||
      (s.project ?? "").toLowerCase().includes(q)
    );
  };
  const visibleRanked = ranked.filter(matches);
  const visibleUngraded = ungraded.filter(matches);

  const name = (s: StudentMeta) =>
    canOpen(s) ? (
      <Link to={`/students/${encodeURIComponent(s.slug)}`}>{s.display_name}</Link>
    ) : (
      <span>{s.display_name}</span>
    );

  return (
    <main className="page">
      {error && <div className="error-banner">{error}</div>}

      <Panel
        title="Student Leaderboard"
        hint="Students ranked by total points across all graded exercises. Equal points share a rank. The bar shows each student's share of the possible points; students who have never been graded are listed at the bottom without a rank."
        toolbar={
          <>
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Search by student or project"
            />
            <span className="toolbar-spacer" />
          </>
        }
        footer={
          <div className="panel-footer">
            <span className="last-updated">
              {lastUpdated ? `Last updated ${lastUpdated}` : ""}
            </span>
            <span>
              {ranked.length} ranked
              {ungraded.length > 0 && ` · ${ungraded.length} not graded yet`}
            </span>
          </div>
        }
      >
        <div className="table-wrap">
          <ol className="board">
            {visibleRanked.map((s) => {
              const earned = s.points_earned ?? 0;
              const possible = s.points_possible ?? 0;
              const pct = possible > 0 ? Math.round((earned / possible) * 100) : null;
              const tier = tierForRatio(earned, possible);
              return (
                <li
                  key={s.slug}
                  className={`board-row${isMe(s) ? " me" : ""}`}
                  title={
                    pct !== null
                      ? `${s.display_name}: ${earned} of ${possible} points (${pct}%)`
                      : `${s.display_name}: ${earned} points`
                  }
                >
                  <RankBadge rank={s.rank} />
                  <span className="board-name">
                    {name(s)}
                    {isMe(s) && <span className="you-chip">You</span>}
                  </span>
                  <span className="board-bar" aria-hidden="true">
                    <span
                      className="board-bar-fill"
                      style={{ width: `${Math.min(pct ?? 0, 100)}%` }}
                    />
                  </span>
                  <span className="board-points">
                    <span className={`pts-chip tier-${tier}`}>
                      {earned}/{possible} pts
                      {pct !== null && <span className="pct">({pct}%)</span>}
                    </span>
                  </span>
                </li>
              );
            })}
            {visibleUngraded.length > 0 && (
              <li className="board-divider" aria-hidden="true">
                Not graded yet
              </li>
            )}
            {visibleUngraded.map((s) => (
              <li key={s.slug} className="board-row unranked">
                <span className="board-rank" aria-label="Unranked">
                  —
                </span>
                <span className="board-name">
                  {name(s)}
                  {isMe(s) && <span className="you-chip">You</span>}
                </span>
                <span className="board-bar" aria-hidden="true" />
                <span className="board-points cell-muted">no grades yet</span>
              </li>
            ))}
            {!loading && visibleRanked.length === 0 && visibleUngraded.length === 0 && (
              <li className="empty-cell">
                <h3>{students.length === 0 ? "No students yet" : "No matches"}</h3>
                {students.length === 0
                  ? "The leaderboard fills in as students are registered and graded."
                  : "No student or project matches that search."}
              </li>
            )}
            {loading && <li className="empty-cell">Loading…</li>}
          </ol>
        </div>
      </Panel>
    </main>
  );
}
