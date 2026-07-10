import { useAuth } from "react-oidc-context";

import { accessTokenScopes } from "./cognito";

/** Cognito-reserved scope that authorizes access-token self-service calls
 * (change password, update attributes, TOTP MFA). */
export const SELF_SERVICE_SCOPE = "aws.cognito.signin.user.admin";

// Cognito hosted UI + Authorization Code + PKCE. The authority is the user
// pool issuer; oidc-client-ts discovers the hosted-UI endpoints from its
// /.well-known/openid-configuration.
export const oidcConfig = {
  authority: import.meta.env.VITE_COGNITO_AUTHORITY ?? "",
  client_id: import.meta.env.VITE_COGNITO_CLIENT_ID ?? "",
  redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  // aws.cognito.signin.user.admin authorizes the in-app Settings dialog to call
  // the Cognito self-service API (password, profile, TOTP MFA) with the access
  // token. Must also be allowed on the app client (cognito-auth Terraform).
  scope: "openid email profile aws.cognito.signin.user.admin",
  // Strip ?code=&state= from the URL after the redirect completes.
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname);
  },
};

export function useToken(): string {
  const auth = useAuth();
  return auth.user?.id_token ?? "";
}

/** The Cognito self-service API (Settings dialog) is authorized by the access
 * token, not the id token the REST API uses. */
export function useAccessToken(): string {
  const auth = useAuth();
  return auth.user?.access_token ?? "";
}

/** The friendly display name (the `name` attribute) if the user set one in
 * Settings, otherwise their email. */
export function useDisplayName(): string {
  const auth = useAuth();
  const name = (auth.user?.profile?.name ?? "").trim();
  return name || (auth.user?.profile?.email ?? "");
}

/** Whether the current access token carries the self-service scope. Sessions
 * signed in before the scope was added won't have it until they re-login. */
export function useHasSelfServiceScope(): boolean {
  const auth = useAuth();
  return accessTokenScopes(auth.user?.access_token ?? "").includes(SELF_SERVICE_SCOPE);
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

/** Admins and mentors can act (grade, register, edit); the `student` group
 * only views. The backend enforces the same matrix — this is cosmetic. */
export function useCanGrade(): boolean {
  const groups = useGroups();
  return groups.includes("admin") || groups.includes("mentor");
}

/** A pure student — in the `student` group and nothing more privileged. Such
 * users are confined to their own grades page (no roster, no exercises tab);
 * anyone who is also admin/mentor gets the full app. The backend enforces the
 * same scoping (GET /v1/students returns only their card) — this is cosmetic. */
export function useIsStudentOnly(): boolean {
  const groups = useGroups();
  return (
    groups.includes("student") &&
    !groups.includes("admin") &&
    !groups.includes("mentor")
  );
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
