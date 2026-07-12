import { useEffect, useState } from "react";
import { useAuth } from "react-oidc-context";
import QRCode from "qrcode";

import { api } from "../api";
import {
  signOut,
  useAccessToken,
  useCanGrade,
  useHasSelfServiceScope,
  useIsAdmin,
  useToken,
} from "../auth";
import {
  associateSoftwareToken,
  changePassword,
  getUser,
  otpauthUri,
  setTotpPreference,
  updateDisplayName,
  verifySoftwareToken,
} from "../cognito";
import { ConfirmModal } from "../components/ConfirmModal";
import { Panel } from "../components/table";
import type { UpdateUserSettingsPayload, UserSettings } from "../types";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Settings page (opened from the Settings item in the top-right user menu):
 * account self-service — display name, password, and TOTP two-factor auth
 * (all three talk to the Cognito self-service API with the access token) —
 * plus, for admins and mentors, their own grading credentials (REST API).
 * Each panel has a single Save in its bottom-right corner that applies every
 * changed field at once; the panels sit side by side for staff. */
export default function Settings() {
  const auth = useAuth();
  const accessToken = useAccessToken();
  const hasScope = useHasSelfServiceScope();
  const canGrade = useCanGrade();
  const email = auth.user?.profile?.email ?? "";

  const [loading, setLoading] = useState(hasScope);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [totpEnabled, setTotpEnabled] = useState(false);

  useEffect(() => {
    if (!hasScope) return;
    let alive = true;
    getUser(accessToken)
      .then((info) => {
        if (!alive) return;
        setName(info.attributes.name ?? "");
        setTotpEnabled(info.totpEnabled);
      })
      .catch((e) => alive && setLoadError(errText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [accessToken, hasScope]);

  const accountPanel = (
    <Panel
      title="Account"
      hint="Your display name, password, and two-factor authentication. One Save applies every change."
    >
      <div className="panel-body">
        {!hasScope ? (
          <div className="warn-banner">
            Your current session predates two-factor support. Sign out and sign
            in again to manage your security settings.
            <div className="settings-actions">
              <button
                type="button"
                className="btn primary"
                onClick={() => signOut(() => auth.removeUser())}
              >
                Sign out
              </button>
            </div>
          </div>
        ) : loadError ? (
          <div className="error-banner">{loadError}</div>
        ) : loading ? (
          <p className="cell-muted">Loading your account…</p>
        ) : (
          <AccountSettings
            accessToken={accessToken}
            initialName={name}
            onNameSaved={(saved) => {
              setName(saved);
              // Refresh tokens so the new display name shows in the
              // header. Best effort — it still updates on the next
              // sign-in if this fails.
              void auth.signinSilent().catch(() => {});
            }}
            account={email}
            totpEnabled={totpEnabled}
            onTotpChange={setTotpEnabled}
          />
        )}
      </div>
    </Panel>
  );

  return (
    <main className={`page settings-page${canGrade ? " wide" : ""}`}>
      {canGrade ? (
        <div className="settings-grid">
          {accountPanel}
          <Panel
            title="Grading"
            hint="Personal credentials and AI model used by the grading jobs you start. Anything left unset falls back to the shared deployment credentials. One Save applies every change."
          >
            <div className="panel-body">
              <GradingSettings />
            </div>
          </Panel>
        </div>
      ) : (
        accountPanel
      )}
    </main>
  );
}

/** Account panel body: display name + password change fields, the MFA
 * enrollment flow, and one Save that applies whichever of the two savable
 * groups (name, password) actually changed. MFA keeps its own buttons — it's
 * an interactive enroll/verify flow, not a saved field. */
function AccountSettings({
  accessToken,
  initialName,
  onNameSaved,
  account,
  totpEnabled,
  onTotpChange,
}: {
  accessToken: string;
  initialName: string;
  onNameSaved: (name: string) => void;
  account: string;
  totpEnabled: boolean;
  onTotpChange: (enabled: boolean) => void;
}) {
  const [name, setName] = useState(initialName);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const nameDirty = name.trim() !== initialName.trim();
  const passwordTouched = Boolean(current || next || confirm);
  const dirty = nameDirty || passwordTouched;

  const save = async () => {
    if (busy || !dirty) return;
    if (passwordTouched) {
      if (!current || !next || !confirm) {
        setNote({
          ok: false,
          text: "Fill in all three password fields to change your password (or clear them to keep it).",
        });
        return;
      }
      if (next === current) {
        setNote({
          ok: false,
          text: "Your new password must be different from your current password.",
        });
        return;
      }
      if (next !== confirm) {
        setNote({ ok: false, text: "The new passwords don't match." });
        return;
      }
    }
    setBusy(true);
    setNote(null);
    // The two groups hit different Cognito APIs — run both, and if the second
    // fails report what did land so the user isn't left guessing.
    const done: string[] = [];
    try {
      if (nameDirty) {
        await updateDisplayName(accessToken, name.trim());
        onNameSaved(name.trim());
        done.push("display name");
      }
      if (passwordTouched) {
        await changePassword(accessToken, current, next);
        setCurrent("");
        setNext("");
        setConfirm("");
        done.push("password");
      }
      setNote({ ok: true, text: `Saved: ${done.join(" and ")}.` });
    } catch (e) {
      setNote({
        ok: false,
        text:
          done.length > 0
            ? `Saved ${done.join(" and ")}, but: ${errText(e)}`
            : errText(e),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="settings-section">
        <h3>Display name</h3>
        <p className="section-hint">
          Shown in the top-right menu. Your login email doesn&rsquo;t change.
        </p>
        <div className="settings-row">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Jane Doe"
            maxLength={100}
          />
        </div>
      </section>

      <section className="settings-section">
        <h3>Password</h3>
        <p className="section-hint">
          At least 12 characters, with upper, lower, and a number. Leave all
          three fields empty to keep your current password.
        </p>
        <div className="settings-row">
          <label>Current password</label>
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <label>New password</label>
          <input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <label>Confirm new password</label>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
      </section>

      <MfaSection
        accessToken={accessToken}
        account={account}
        enabled={totpEnabled}
        onChange={onTotpChange}
      />

      <div className="settings-footer">
        {note && (
          <span className={`settings-note ${note.ok ? "ok" : "err"}`}>{note.text}</span>
        )}
        <button
          type="button"
          className="btn primary"
          onClick={() => void save()}
          disabled={!dirty || busy}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}

function MfaSection({
  accessToken,
  account,
  enabled,
  onChange,
}: {
  accessToken: string;
  account: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  // Enrollment state: null = not enrolling; otherwise the secret + its QR.
  const [secret, setSecret] = useState<string | null>(null);
  const [qr, setQr] = useState<string>("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingDisable, setConfirmingDisable] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const beginEnroll = async () => {
    setBusy(true);
    setNote(null);
    try {
      const s = await associateSoftwareToken(accessToken);
      setSecret(s);
      setQr(await QRCode.toDataURL(otpauthUri(s, account), { margin: 1, width: 168 }));
      setCode("");
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  const finishEnroll = async () => {
    if (busy || code.trim().length < 6) return;
    setBusy(true);
    setNote(null);
    try {
      await verifySoftwareToken(accessToken, code.trim());
      await setTotpPreference(accessToken, true);
      setSecret(null);
      setQr("");
      onChange(true);
      setNote({ ok: true, text: "Two-factor authentication is on. You'll be asked for a code next time you sign in." });
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  // Runs inside the confirmation dialog: a throw keeps the dialog open with
  // the error; on success it closes and the section flips back to "off".
  const disable = async () => {
    setNote(null);
    await setTotpPreference(accessToken, false);
    onChange(false);
    setConfirmingDisable(false);
    setNote({ ok: true, text: "Two-factor authentication is off." });
  };

  return (
    <section className="settings-section">
      <h3>Two-factor authentication</h3>
      <p className="section-hint">Protect your account with a time-based code from an authenticator app. Changes here apply immediately — no Save needed.</p>

      <div className="settings-actions">
        {!enabled && !secret && (
          <button type="button" className="btn primary" onClick={() => void beginEnroll()} disabled={busy}>
            {busy ? "Starting…" : "Set up authenticator app"}
          </button>
        )}
        {enabled && (
          <button type="button" className="btn" onClick={() => setConfirmingDisable(true)} disabled={busy}>
            Turn off
          </button>
        )}
        <span className={`mfa-status ${enabled ? "on" : "off"}`}>
          {enabled ? "● Enabled" : "○ Not enabled"}
        </span>
      </div>

      {secret && (
        <div className="mfa-setup">
          {qr && <img className="mfa-qr" src={qr} alt="Authenticator setup QR code" width={168} height={168} />}
          <div className="mfa-setup-right">
            <p className="section-hint">
              Scan this with Google Authenticator, 1Password, Authy, etc. — or enter the key manually:
            </p>
            <code className="mfa-secret">{secret}</code>
            <label className="mfa-label">Enter the 6-digit code to confirm</label>
            <input
              className="mfa-code-input"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="123456"
            />
            <div className="settings-actions">
              <button type="button" className="btn primary" onClick={() => void finishEnroll()} disabled={busy || code.length < 6}>
                {busy ? "Verifying…" : "Verify & enable"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setSecret(null);
                  setQr("");
                  setNote(null);
                }}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {note && <p className={`settings-note mfa-result ${note.ok ? "ok" : "err"}`}>{note.text}</p>}

      {confirmingDisable && (
        <ConfirmModal
          title="Turn Off Two-Factor Authentication"
          confirmLabel="Turn off two-factor auth"
          busyLabel="Turning off…"
          onConfirm={disable}
          onClose={() => setConfirmingDisable(false)}
        >
          <p>
            Turn off two-factor authentication for <strong>{account}</strong>?
            Your enrolled authenticator app is removed immediately, and signing
            in will only require your password.
          </p>
          <p className="hint">
            To turn it back on later you must set it up from the beginning:
            scan a new QR code and verify a fresh 6-digit code.
          </p>
        </ConfirmModal>
      )}
    </section>
  );
}

/** Personal grading credentials (REST API, not Cognito): the caller's own
 * SnapLogic login (admins), Anthropic API key, and judge model. Anything
 * left unset falls back to the shared deployment credentials. Secrets are
 * write-only — the API only reports that one is stored. */
function GradingSettings() {
  const token = useToken();
  const isAdmin = useIsAdmin();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getSettings(token)
      .then((r) => alive && setSettings(r.settings))
      .catch((e) => alive && setLoadError(errText(e)));
    return () => {
      alive = false;
    };
  }, [token]);

  if (loadError) {
    return <div className="error-banner">{loadError}</div>;
  }
  if (!settings) {
    return <p className="cell-muted">Loading your credentials…</p>;
  }
  return (
    <GradingSettingsForm
      token={token}
      isAdmin={isAdmin}
      settings={settings}
      onSaved={setSettings}
    />
  );
}

/** All grading fields in one form with a single Save: it builds one payload
 * from whichever fields changed and PATCHes them in one call. The per-secret
 * Clear buttons stay — clearing is its own action, not a save. */
function GradingSettingsForm({
  token,
  isAdmin,
  settings,
  onSaved,
}: {
  token: string;
  isAdmin: boolean;
  settings: UserSettings;
  onSaved: (s: UserSettings) => void;
}) {
  const [username, setUsername] = useState(settings.snaplogic_username ?? "");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  // No "project default" option — a null judge_model shows as the default
  // model itself, and picking it stores the id explicitly.
  const [model, setModel] = useState(settings.judge_model ?? settings.default_model);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const usernameDirty = isAdmin && username.trim() !== (settings.snaplogic_username ?? "");
  const passwordDirty = isAdmin && password !== "";
  const keyDirty = apiKey.trim() !== "";
  const modelDirty = model !== (settings.judge_model ?? settings.default_model);
  const dirty = usernameDirty || passwordDirty || keyDirty || modelDirty;

  const push = async (
    payload: UpdateUserSettingsPayload,
    okText: string,
  ): Promise<boolean> => {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.updateSettings(token, payload);
      onSaved(r.settings);
      setNote({ ok: true, text: okText });
      return true;
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (busy || !dirty) return;
    const payload: UpdateUserSettingsPayload = {};
    if (usernameDirty) payload.snaplogic_username = username.trim() || null;
    if (passwordDirty) payload.snaplogic_password = password;
    if (keyDirty) payload.anthropic_api_key = apiKey.trim();
    if (modelDirty) payload.judge_model = model;
    if (await push(payload, "Grading settings saved.")) {
      setPassword("");
      setApiKey("");
    }
  };

  const clearSnapLogic = async () => {
    if (
      await push(
        { snaplogic_username: null, snaplogic_password: null },
        "SnapLogic credentials cleared — jobs you start use the shared credentials again.",
      )
    ) {
      setUsername("");
      setPassword("");
    }
  };

  const clearKey = async () => {
    if (
      await push(
        { anthropic_api_key: null },
        "Key cleared — gradings you start bill the shared key again.",
      )
    ) {
      setApiKey("");
    }
  };

  const hasStoredCreds =
    Boolean(settings.snaplogic_username) || settings.snaplogic_password_set;
  const savedKeyHint = settings.anthropic_api_key_set
    ? `Saved key ${settings.anthropic_api_key_hint ?? ""}`.trim()
    : "";

  return (
    <>
      {isAdmin && (
        <section className="settings-section">
          <h3>My SnapLogic credentials</h3>
          <p className="section-hint">
            Gradings, syncs, and student registrations you start run under this
            login instead of the shared deployment credentials. Both fields must
            be set for it to take effect.
          </p>
          <div className="settings-row">
            <label>SnapLogic username</label>
            <input
              type="text"
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="you@example.com"
            />
            <label>SnapLogic password</label>
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={settings.snaplogic_password_set ? "•••••••• (saved)" : ""}
            />
          </div>
          {hasStoredCreds && (
            <div className="settings-actions">
              <button
                type="button"
                className="btn"
                onClick={() => void clearSnapLogic()}
                disabled={busy}
              >
                Clear
              </button>
            </div>
          )}
        </section>
      )}

      <section className="settings-section">
        <h3>My Anthropic API key</h3>
        <p className="section-hint">
          Gradings you start are billed to this key instead of the shared one.
        </p>
        <div className="settings-row">
          <label>API key</label>
          <input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={savedKeyHint || "sk-ant-…"}
          />
        </div>
        {settings.anthropic_api_key_set && (
          <div className="settings-actions">
            <button
              type="button"
              className="btn"
              onClick={() => void clearKey()}
              disabled={busy}
            >
              Clear
            </button>
          </div>
        )}
      </section>

      <section className="settings-section">
        <h3>AI judge model</h3>
        <p className="section-hint">
          The Claude model that evaluates the gradings you start. More capable
          models cost more per grading run.
        </p>
        <div className="settings-row">
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {settings.allowed_models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label} · {m.description}
              </option>
            ))}
          </select>
        </div>
      </section>

      <div className="settings-footer">
        {note && (
          <span className={`settings-note ${note.ok ? "ok" : "err"}`}>{note.text}</span>
        )}
        <button
          type="button"
          className="btn primary"
          onClick={() => void save()}
          disabled={!dirty || busy}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}
