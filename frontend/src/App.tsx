import { useAuth } from "react-oidc-context";
import { NavLink, Route, Routes } from "react-router-dom";

import { signOut, useGroups } from "./auth";
import Dashboard from "./pages/Dashboard";
import Exercises from "./pages/Exercises";
import StudentDetail from "./pages/StudentDetail";

function Login({ onLogin, error }: { onLogin: () => void; error?: string }) {
  return (
    <div className="login-page">
      <div className="login-card">
        <h1>SnapLogic Exercise Grades</h1>
        <p>Sign in with your mentor or admin account to continue.</p>
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
  const groups = useGroups();
  const email = auth.user?.profile?.email ?? "";
  return (
    <>
      <header className="site-header">
        <h1>SnapLogic Exercise Grades</h1>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Dashboard
          </NavLink>
          <NavLink
            to="/exercises"
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            Exercises
          </NavLink>
          <span className="who">
            {email}
            {groups.map((g) => (
              <span className="role-chip" key={g}>
                {g}
              </span>
            ))}
          </span>
          <button className="btn" onClick={() => signOut(() => auth.removeUser())}>
            Sign out
          </button>
        </nav>
      </header>
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
          <h1>SnapLogic Exercise Grades</h1>
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
