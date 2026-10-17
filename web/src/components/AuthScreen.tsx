import { Camera, ShieldCheck, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api, ApiError, saveSession, type Session } from "../api";
import { useT } from "../i18n";

export function AuthScreen({ onAuthed }: { onAuthed: (session: Session) => void }) {
  const t = useT();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result =
        mode === "login"
          ? await api.login({ email, password })
          : await api.register({ email, password, display_name: name, locale: t.locale });
      const session = { token: result.access_token, user: result.user };
      saveSession(session);
      onAuthed(session);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError(t("err_login"));
      else if (err instanceof ApiError && err.status === 409) setError(t("err_exists"));
      else if (err instanceof ApiError && err.status === 422) setError(err.message);
      else setError(t("err_generic"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="screen auth">
      <div className="brand">
        <img src="/icon.svg" alt="" width={76} height={76} />
        <h1>{t("appName")}</h1>
        <p className="muted">{t("tagline")}</p>
      </div>
      <ul className="values">
        <li><span className="icon-circle"><Camera size={20} aria-hidden /></span>{t("valueSnap")}</li>
        <li><span className="icon-circle info"><Sparkles size={20} aria-hidden /></span>{t("valueIdentify")}</li>
        <li><span className="icon-circle accent"><ShieldCheck size={20} aria-hidden /></span>{t("valueProtect")}</li>
      </ul>
      <form className="card stack" onSubmit={submit}>
        {mode === "register" && (
          <label>
            {t("displayName")}
            <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
          </label>
        )}
        <label>
          {t("email")}
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label>
          {t("password")}
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>
          {mode === "login" ? t("signIn") : t("createAccount")}
        </button>
        <button
          type="button"
          className="link"
          style={{ alignSelf: "center" }}
          onClick={() => setMode(mode === "login" ? "register" : "login")}
        >
          {mode === "login" ? t("needAccount") : t("haveAccount")}
        </button>
      </form>
    </main>
  );
}
