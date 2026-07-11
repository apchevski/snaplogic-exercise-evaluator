import { useEffect, useState } from "react";
import { useAuth } from "react-oidc-context";
import QRCode from "qrcode";

import { signOut, useAccessToken, useHasSelfServiceScope } from "../auth";
import {
  associateSoftwareToken,
  changePassword,
  getUser,
  otpauthUri,
  setTotpPreference,
  updateDisplayName,
  verifySoftwareToken,
} from "../cognito";
import {
  IconCheck,
  IconClose,
  IconKey,
  IconLogout,
  IconShield,
} from "./icons";

interface Props {
  /** Called after the display name changes so the shell can refresh its
   * tokens and update the header. */
  onProfileChanged: () => void;
  onClose: () => void;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Account settings: display name, password, and TOTP two-factor auth. All
 * three talk to the Cognito self-service API with the access token. */
export function SettingsModal({ onProfileChanged, onClose }: Props) {
  const auth = useAuth();
  const accessToken = useAccessToken();
  const hasScope = useHasSelfServiceScope();
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

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal" role="dialog" aria-label="Settings">
        <header>
          <h2>Settings</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="modal-body">
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
                  <IconLogout />
                  Sign out
                </button>
              </div>
            </div>
          ) : loadError ? (
            <div className="error-banner">{loadError}</div>
          ) : loading ? (
            <p className="cell-muted">Loading your account…</p>
          ) : (
            <>
              <ProfileSection
                accessToken={accessToken}
                initialName={name}
                onSaved={(saved) => {
                  setName(saved);
                  onProfileChanged();
                }}
              />
              <PasswordSection accessToken={accessToken} />
              <MfaSection
                accessToken={accessToken}
                account={email}
                enabled={totpEnabled}
                onChange={setTotpEnabled}
              />
            </>
          )}
        </div>
        <footer>
          <button type="button" className="btn" onClick={onClose}>
            <IconClose />
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}

function ProfileSection({
  accessToken,
  initialName,
  onSaved,
}: {
  accessToken: string;
  initialName: string;
  onSaved: (name: string) => void;
}) {
  const [value, setValue] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const dirty = value.trim() !== initialName.trim();

  const save = async () => {
    if (busy || !dirty) return;
    setBusy(true);
    setNote(null);
    try {
      await updateDisplayName(accessToken, value.trim());
      onSaved(value.trim());
      setNote({ ok: true, text: "Display name saved." });
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section">
      <h3>Display name</h3>
      <p className="section-hint">Shown in the top-right menu. Your login email doesn&rsquo;t change.</p>
      <div className="settings-row">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. Jane Doe"
          maxLength={100}
        />
      </div>
      <div className="settings-actions">
        <button type="button" className="btn primary" onClick={() => void save()} disabled={!dirty || busy}>
          <IconCheck />
          {busy ? "Saving…" : "Save"}
        </button>
        {note && <span className={`settings-note ${note.ok ? "ok" : "err"}`}>{note.text}</span>}
      </div>
    </section>
  );
}

function PasswordSection({ accessToken }: { accessToken: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const ready = current && next && confirm && !busy;

  const submit = async () => {
    if (!ready) return;
    if (next === current) {
      setNote({ ok: false, text: "Your new password must be different from your current password." });
      return;
    }
    if (next !== confirm) {
      setNote({ ok: false, text: "The new passwords don't match." });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      await changePassword(accessToken, current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setNote({ ok: true, text: "Password changed." });
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section">
      <h3>Password</h3>
      <p className="section-hint">At least 12 characters, with upper, lower, and a number.</p>
      <form
        className="settings-row"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label>Current password</label>
        <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        <label>New password</label>
        <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <label>Confirm new password</label>
        <input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <div className="settings-actions">
          <button type="submit" className="btn primary" disabled={!ready}>
            <IconKey />
            {busy ? "Changing…" : "Change password"}
          </button>
          {note && <span className={`settings-note ${note.ok ? "ok" : "err"}`}>{note.text}</span>}
        </div>
      </form>
    </section>
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

  const disable = async () => {
    setBusy(true);
    setNote(null);
    try {
      await setTotpPreference(accessToken, false);
      onChange(false);
      setNote({ ok: true, text: "Two-factor authentication is off." });
    } catch (e) {
      setNote({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section">
      <h3>Two-factor authentication</h3>
      <p className="section-hint">Protect your account with a time-based code from an authenticator app.</p>

      <div className="settings-actions">
        {!enabled && !secret && (
          <button type="button" className="btn primary" onClick={() => void beginEnroll()} disabled={busy}>
            <IconShield />
            {busy ? "Starting…" : "Set up authenticator app"}
          </button>
        )}
        {enabled && (
          <button type="button" className="btn" onClick={() => void disable()} disabled={busy}>
            <IconClose />
            {busy ? "Working…" : "Turn off"}
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
                <IconCheck />
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
                <IconClose />
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {note && <p className={`settings-note mfa-result ${note.ok ? "ok" : "err"}`}>{note.text}</p>}
    </section>
  );
}
