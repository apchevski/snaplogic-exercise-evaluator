import { useAuth } from "react-oidc-context";

// Cognito hosted UI + Authorization Code + PKCE. The authority is the user
// pool issuer; oidc-client-ts discovers the hosted-UI endpoints from its
// /.well-known/openid-configuration.
export const oidcConfig = {
  authority: import.meta.env.VITE_COGNITO_AUTHORITY ?? "",
  client_id: import.meta.env.VITE_COGNITO_CLIENT_ID ?? "",
  redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  scope: "openid email profile",
  // Strip ?code=&state= from the URL after the redirect completes.
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname);
  },
};

export function useToken(): string {
  const auth = useAuth();
  return auth.user?.id_token ?? "";
}

export function useGroups(): string[] {
  const auth = useAuth();
  const raw = auth.user?.profile?.["cognito:groups"];
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") return raw.replace(/[[\]]/g, "").split(/[\s,]+/).filter(Boolean);
  return [];
}

export function useIsAdmin(): boolean {
  return useGroups().includes("admin");
}

/** Cognito has no end_session in some pool configs — log out via the hosted UI. */
export function signOut(removeUser: () => Promise<void>): void {
  const domain = (import.meta.env.VITE_COGNITO_DOMAIN ?? "").replace(/\/$/, "");
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID ?? "";
  void removeUser().then(() => {
    if (domain && clientId) {
      const logoutUri = encodeURIComponent(`${window.location.origin}/`);
      window.location.href = `${domain}/logout?client_id=${clientId}&logout_uri=${logoutUri}`;
    } else {
      window.location.href = "/";
    }
  });
}
