// Self-service calls to the Amazon Cognito user-pools JSON API, authorized by
// the signed-in user's ACCESS token (not the id token the REST API uses). These
// operations need the app client to grant `aws.cognito.signin.user.admin` (see
// SELF_SERVICE_SCOPE in auth.ts and the cognito-auth Terraform module). No AWS
// credentials or SigV4 signing are involved — the access token is the whole
// authorization — so this is a plain fetch to the regional endpoint. The TOTP
// secret produced during enrollment is rendered locally and never leaves the
// browser.

// VITE_COGNITO_AUTHORITY looks like
//   https://cognito-idp.<region>.amazonaws.com/<userPoolId>
const AUTHORITY = import.meta.env.VITE_COGNITO_AUTHORITY ?? "";
const REGION = (AUTHORITY.match(/cognito-idp\.([^.]+)\.amazonaws\.com/) ?? [])[1] ?? "";
const ENDPOINT = `https://cognito-idp.${REGION}.amazonaws.com/`;

/** A Cognito API error, carrying the service error code (e.g.
 * `NotAuthorizedException`, `LimitExceededException`) alongside the message. */
export class CognitoError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "CognitoError";
    this.code = code;
  }
}

async function call<T>(target: string, body: Record<string, unknown>): Promise<T> {
  const resp = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  const data = text ? JSON.parse(text) : {};
  if (!resp.ok) {
    // Error bodies look like { "__type": "com.amazon...#InvalidParameterException",
    // "message": "..." }. Keep just the exception name.
    const code = String(data.__type ?? "UnknownError").split(/[#.]/).pop() ?? "UnknownError";
    throw new CognitoError(code, data.message ?? data.Message ?? "Request failed.");
  }
  return data as T;
}

export interface UserInfo {
  /** Standard attributes keyed by name (e.g. `email`, `name`). */
  attributes: Record<string, string>;
  /** Whether a TOTP authenticator is currently registered and enabled. */
  totpEnabled: boolean;
}

export async function getUser(accessToken: string): Promise<UserInfo> {
  const data = await call<{
    UserAttributes?: { Name: string; Value: string }[];
    UserMFASettingList?: string[];
  }>("GetUser", { AccessToken: accessToken });
  const attributes: Record<string, string> = {};
  for (const a of data.UserAttributes ?? []) attributes[a.Name] = a.Value;
  return {
    attributes,
    totpEnabled: (data.UserMFASettingList ?? []).includes("SOFTWARE_TOKEN_MFA"),
  };
}

export async function updateDisplayName(accessToken: string, name: string): Promise<void> {
  await call("UpdateUserAttributes", {
    AccessToken: accessToken,
    UserAttributes: [{ Name: "name", Value: name }],
  });
}

export async function changePassword(
  accessToken: string,
  previous: string,
  proposed: string,
): Promise<void> {
  await call("ChangePassword", {
    AccessToken: accessToken,
    PreviousPassword: previous,
    ProposedPassword: proposed,
  });
}

/** Step 1 of TOTP enrollment: get a fresh shared secret (base32, ready to hand
 * to an authenticator app). Authorized by the access token. */
export async function associateSoftwareToken(accessToken: string): Promise<string> {
  const data = await call<{ SecretCode: string }>("AssociateSoftwareToken", {
    AccessToken: accessToken,
  });
  return data.SecretCode;
}

/** Step 2: prove the authenticator is set up by submitting a live code. Throws
 * on a wrong/expired code. */
export async function verifySoftwareToken(accessToken: string, userCode: string): Promise<void> {
  const data = await call<{ Status: string }>("VerifySoftwareToken", {
    AccessToken: accessToken,
    UserCode: userCode,
    FriendlyDeviceName: "Authenticator app",
  });
  if (data.Status !== "SUCCESS") {
    throw new CognitoError("VerificationFailed", "That code could not be verified. Try again.");
  }
}

/** Step 3 (and the disable path): turn the verified TOTP factor on or off as the
 * user's preferred MFA method. */
export async function setTotpPreference(accessToken: string, enabled: boolean): Promise<void> {
  await call("SetUserMFAPreference", {
    AccessToken: accessToken,
    SoftwareTokenMfaSettings: { Enabled: enabled, PreferredMfa: enabled },
  });
}

/** The scopes granted to a Cognito access token, read from its `scope` claim (a
 * JWT). This is the authoritative source — Cognito's token endpoint doesn't
 * return a `scope` field to the OIDC client, but the access token always carries
 * the claim. Used to tell whether self-service calls are authorized before we
 * attempt them. */
export function accessTokenScopes(accessToken: string): string[] {
  const payload = accessToken.split(".")[1];
  if (!payload) return [];
  try {
    const json = decodeURIComponent(
      atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
        .split("")
        .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
        .join(""),
    );
    const scope = (JSON.parse(json) as { scope?: string }).scope;
    return typeof scope === "string" ? scope.split(/\s+/).filter(Boolean) : [];
  } catch {
    return [];
  }
}

/** The otpauth:// URI encoded into the enrollment QR code. */
export function otpauthUri(secret: string, account: string): string {
  const issuer = "SnapLogic Evaluator";
  const label = encodeURIComponent(`${issuer}:${account}`);
  return `otpauth://totp/${label}?secret=${secret}&issuer=${encodeURIComponent(issuer)}`;
}
