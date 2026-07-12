import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "react-oidc-context";
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import { api, onUnauthorized } from "./api";
import {
  signOut,
  useDisplayName,
  useGroups,
  useIsStudentOnly,
  useToken,
} from "./auth";
import { IconLogin, IconLogout, IconSettings } from "./components/icons";
import Dashboard from "./pages/Dashboard";
import Exercises from "./pages/Exercises";
import Ranking from "./pages/Ranking";
import Settings from "./pages/Settings";
import StudentDetail from "./pages/StudentDetail";

function BrandMark() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="5" fill="#1c4e80" />
      <circle cx="8" cy="8" r="1.7" fill="#ffffff" />
      <circle cx="16" cy="8" r="1.7" fill="#ffffff" />
      <circle cx="8" cy="16" r="1.7" fill="#ffffff" />
      <circle cx="16" cy="16" r="1.7" fill="#ffffff" />
      <circle cx="12" cy="12" r="1.7" fill="#9fc3e8" />
    </svg>
  );
}

function Brand() {
  return (
    <div className="brand">
      <BrandMark />
      <span className="brand-word">SnapLogic</span>
      <span className="brand-sub">Exercise Evaluator</span>
    </div>
  );
}

/** Initials from a display name ("Jane Doe" → "JD") or an email
 * ("jane.doe@acme.com" → "JD"). */
function initialsFor(label: string): string {
  const parts = label.split("@")[0].split(/[^a-zA-Z0-9]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]);
  return (letters.join("") || "U").toUpperCase();
}

function UserMenu() {
  const auth = useAuth();
  const groups = useGroups();
  const displayName = useDisplayName();
  const navigate = useNavigate();
  const email = auth.user?.profile?.email ?? "";
  const [open, setOpen] = useState(false);
  return (
    <div className="user-cluster">
      <button
        className="user-button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="avatar">{initialsFor(displayName)}</span>
        <span className="user-email">{displayName}</span>
        <span className="caret" aria-hidden="true">
          ▼
        </span>
      </button>
      {open && (
        <>
          <div className="menu-backdrop" onClick={() => setOpen(false)} />
          <div className="user-menu" role="menu">
            {email && displayName !== email && (
              <div className="user-menu-email">{email}</div>
            )}
            {groups.length > 0 && (
              <div className="user-menu-roles">
                {groups.map((g) => (
                  <span className="role-chip" key={g}>
                    {g}
                  </span>
                ))}
              </div>
            )}
            <button
              className="user-menu-item"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                navigate("/settings");
              }}
            >
              <IconSettings />
              Settings
            </button>
            <button
              className="user-menu-item"
              role="menuitem"
              onClick={() => signOut(() => auth.removeUser())}
            >
              <IconLogout />
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Login({ onLogin, error }: { onLogin: () => void; error?: string }) {
  return (
    <div className="login-page">
      <div className="login-card">
        <Brand />
        <p>Sign in with your admin, mentor, or student account to continue.</p>
        {error && <div className="error-banner">{error}</div>}
        <button className="btn primary" onClick={onLogin}>
          <IconLogin />
          Sign in
        </button>
      </div>
    </div>
  );
}

/** White top bar with the brand on the left, the main tabs centered (like
 * the classic SnapLogic console's header), and the user menu on the right. */
function TopBar({ nav }: { nav: ReactNode }) {
  return (
    <header className="topbar">
      <Brand />
      <nav className="topnav">{nav}</nav>
      <UserMenu />
    </header>
  );
}

/** Full app for admins and mentors: Students / Ranking / Exercises tabs,
 * every page, and the Settings page (Account + Grading) behind the user menu. */
function StaffShell() {
  const { pathname } = useLocation();
  const studentsActive = pathname === "/" || pathname.startsWith("/students");
  return (
    <>
      <TopBar
        nav={
          <>
            <NavLink to="/" className={studentsActive ? "active" : ""}>
              Students
            </NavLink>
            <NavLink
              to="/ranking"
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              Ranking
            </NavLink>
            <NavLink
              to="/exercises"
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              Exercises
            </NavLink>
          </>
        }
      />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/students/:slug" element={<StudentDetail />} />
        <Route path="/ranking" element={<Ranking />} />
        <Route path="/exercises" element={<Exercises />} />
        <Route path="/settings" element={<Settings />} />
        {/* The Settings page lived at /manager before the Manager tab was
            retired; keep old bookmarks working. */}
        <Route path="/manager" element={<Navigate to="/settings" replace />} />
        {/* /login only exists while signed out; a signed-in user who lands on
            it (bookmark, back button) goes home. */}
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </>
  );
}

/** A student may only ever be on their OWN detail page; any other slug (or
 * path) bounces to it. The backend 403s a mismatched slug regardless — this
 * just keeps the URL honest. */
function OwnStudentGuard({ ownSlug }: { ownSlug: string }) {
  const { slug } = useParams();
  if (slug !== ownSlug) {
    return <Navigate to={`/students/${encodeURIComponent(ownSlug)}`} replace />;
  }
  return <StudentDetail />;
}

/** Read-only shell for the `student` role: the full Students table (every
 * row, no actions), the Ranking leaderboard, a My Grades page for their OWN
 * detailed evaluation, a read-only Exercises catalog, and the Settings page
 * for their own account.
 * The student's slug isn't in the token, so we resolve it from
 * GET /v1/students: the roster is visible to everyone, but only the caller's
 * own card still carries an email (the backend strips it from other rows) —
 * that match links this login to its card. */
function StudentShell() {
  const auth = useAuth();
  const token = useToken();
  const myEmail = (auth.user?.profile?.email ?? "").trim().toLowerCase();
  // undefined = still loading; null = no card is linked to this login.
  const [slug, setSlug] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listStudents(token)
      .then(({ students }) => {
        if (cancelled) return;
        const own = students.find(
          (s) => (s.email ?? "").trim().toLowerCase() === myEmail,
        );
        setSlug(own?.slug ?? null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [token, myEmail]);

  // A login with no linked card can still browse the roster and exercises;
  // it just has no My Grades tab until a mentor registers their card.
  const topbar = (
    <TopBar
      nav={
        <>
          <NavLink to="/" className={({ isActive }) => (isActive ? "active" : "")} end>
            Students
          </NavLink>
          <NavLink
            to="/ranking"
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            Ranking
          </NavLink>
          {slug && (
            <NavLink
              to={`/students/${encodeURIComponent(slug)}`}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              My Grades
            </NavLink>
          )}
          <NavLink
            to="/exercises"
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            Exercises
          </NavLink>
        </>
      }
    />
  );

  if (error) {
    return (
      <>
        {topbar}
        <main className="page">
          <div className="error-banner">{error}</div>
        </main>
      </>
    );
  }
  if (slug === undefined) {
    return (
      <>
        {topbar}
        <main className="page">Loading…</main>
      </>
    );
  }
  return (
    <>
      {topbar}
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/ranking" element={<Ranking />} />
        <Route path="/exercises" element={<Exercises />} />
        <Route path="/settings" element={<Settings />} />
        {/* The Settings page lived at /manager before the Manager tab was
            retired; keep old bookmarks working. */}
        <Route path="/manager" element={<Navigate to="/settings" replace />} />
        {slug && (
          <Route
            path="/students/:slug"
            element={<OwnStudentGuard ownSlug={slug} />}
          />
        )}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

function Shell() {
  const isStudentOnly = useIsStudentOnly();
  return isStudentOnly ? <StudentShell /> : <StaffShell />;
}

export default function App() {
  const auth = useAuth();
  const [sessionExpired, setSessionExpired] = useState(false);

  // A dead session must return the user to the login screen, not strand them on
  // a page full of "Unauthorized" errors. react-oidc-context keeps the stale
  // user in its store after the refresh token expires, so isAuthenticated stays
  // true on its own — we have to clear it. Two triggers cover it: the access
  // token expiring without a successful silent renew (fires even while the user
  // sits idle), and any API call coming back 401 (catches clock skew or a token
  // the backend rejects for other reasons). Both clear the session; removeUser
  // flips isAuthenticated to false, dropping us to <Login> below.
  useEffect(() => {
    const expire = () => {
      setSessionExpired(true);
      void auth.removeUser();
    };
    const unsubscribe = auth.events.addAccessTokenExpired(expire);
    onUnauthorized(expire);
    return () => {
      unsubscribe();
      onUnauthorized(null);
    };
  }, [auth]);

  // Only take over the whole screen on the initial sign-in. A background
  // silent token renew (e.g. after a display-name change) also flips
  // isLoading, and blanking the app there would unmount the Settings dialog
  // mid-edit — so keep the shell mounted while we already have a session.
  if (auth.isLoading && !auth.isAuthenticated) {
    return (
      <div className="login-page">
        <div className="login-card">
          <Brand />
          <p>Signing in…</p>
        </div>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    const login = (
      <Login
        onLogin={() => void auth.signinRedirect()}
        error={
          sessionExpired
            ? "Your session expired. Please sign in again."
            : auth.error?.message
        }
      />
    );
    // The login screen lives at /login; every other path redirects there while
    // signed out (replace: no dead history entry behind the back button). The
    // OIDC redirect_uri stays "/", so Cognito still returns to the dashboard
    // after a successful sign-in — /login isn't a registered callback URL.
    return (
      <Routes>
        <Route path="/login" element={login} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return <Shell />;
}
