import { useState } from "react";
import { useAuth } from "react-oidc-context";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";

import { signOut, useDisplayName, useGroups } from "./auth";
import { SettingsModal } from "./components/SettingsModal";
import Dashboard from "./pages/Dashboard";
import Exercises from "./pages/Exercises";
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

function UserMenu({ onOpenSettings }: { onOpenSettings: () => void }) {
  const auth = useAuth();
  const groups = useGroups();
  const displayName = useDisplayName();
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
                onOpenSettings();
              }}
            >
              Settings
            </button>
            <button
              className="user-menu-item"
              role="menuitem"
              onClick={() => signOut(() => auth.removeUser())}
            >
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
        <h1>Exercise Grades</h1>
        <p>Sign in with your admin, mentor, or student account to continue.</p>
        {error && <div className="error-banner">{error}</div>}
        <button className="btn primary" onClick={onLogin}>
          Sign in
        </button>
      </div>
    </div>
  );
}

function Shell() {
  const auth = useAuth();
  const { pathname } = useLocation();
  const studentsActive = pathname === "/" || pathname.startsWith("/students");
  const [settingsOpen, setSettingsOpen] = useState(false);
  return (
    <>
      <header className="topbar">
        <Brand />
        <UserMenu onOpenSettings={() => setSettingsOpen(true)} />
      </header>
      {settingsOpen && (
        <SettingsModal
          // Refresh tokens so a new display name shows in the header. Best
          // effort — it still updates on the next sign-in if this fails.
          onProfileChanged={() => void auth.signinSilent().catch(() => {})}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      <nav className="subnav">
        <NavLink to="/" className={studentsActive ? "active" : ""}>
          Students
        </NavLink>
        <NavLink
          to="/exercises"
          className={({ isActive }) => (isActive ? "active" : "")}
        >
          Exercises
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/students/:slug" element={<StudentDetail />} />
        <Route path="/exercises" element={<Exercises />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </>
  );
}

export default function App() {
  const auth = useAuth();

  if (auth.isLoading) {
    return (
      <div className="login-page">
        <div className="login-card">
          <Brand />
          <h1>Exercise Grades</h1>
          <p>Signing in…</p>
        </div>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <Login
        onLogin={() => void auth.signinRedirect()}
        error={auth.error?.message}
      />
    );
  }
  return <Shell />;
}
