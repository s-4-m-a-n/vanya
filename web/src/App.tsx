import { Camera, CloudOff, Leaf, ShieldCheck, UploadCloud, UserRound } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, loadSession, saveSession, type Session } from "./api";
import { AuthScreen } from "./components/AuthScreen";
import { CaptureScreen } from "./components/CaptureScreen";
import { ConsentScreen } from "./components/ConsentScreen";
import { ObservationDetail } from "./components/ObservationDetail";
import { ProgressScreen } from "./components/ProgressScreen";
import { ReviewScreen } from "./components/ReviewScreen";
import { SightingsScreen, type SightingFilter } from "./components/SightingsScreen";
import { clearUserData, getDb } from "./db";
import { isLocale, LOCALES, LocaleContext, translate, type Locale } from "./i18n";
import { syncManager, useDataVersion, useOnline } from "./syncManager";
import type { User } from "./types";

type View =
  | { name: "capture" }
  | { name: "sightings"; filter?: SightingFilter }
  | { name: "review" }
  | { name: "me" }
  | { name: "detail"; id: string };

const REVIEWER_ROLES = new Set(["verifier", "admin"]);

const LAST_USER_KEY = "dh.lastUser";

function initialLocale(session: Session | null): Locale {
  const preferred = session?.user.locale ?? navigator.language.slice(0, 2);
  return isLocale(preferred) ? preferred : "en";
}

function readLastUser(): string | null {
  try {
    return localStorage.getItem(LAST_USER_KEY);
  } catch {
    return null;
  }
}

function writeLastUser(id: string): void {
  try {
    localStorage.setItem(LAST_USER_KEY, id);
  } catch {
    // Without storage we can't detect a user switch; the queue is cleared on sign-out anyway.
  }
}

export function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [locale, setLocale] = useState<Locale>(() => initialLocale(session));
  const [view, setView] = useState<View>({ name: "capture" });
  const [pending, setPending] = useState(0);
  const [needsId, setNeedsId] = useState(0);
  const [hasSightings, setHasSightings] = useState(true);
  const online = useOnline();
  const version = useDataVersion();
  const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
    translate(locale, key, vars);

  const updateUser = useCallback((user: User) => {
    setSession((current) => {
      if (!current) return current;
      const next = { ...current, user };
      saveSession(next);
      return next;
    });
  }, []);

  // Session expiry keeps queued captures so they upload after signing back in.
  const expire = useCallback(() => {
    saveSession(null);
    setSession(null);
  }, []);

  useEffect(() => {
    syncManager.onAuthExpired = expire;
  }, [expire]);

  const consented = !!session?.user.location_consent_at;
  useEffect(() => {
    if (!consented) return;
    syncManager.start();
    return () => syncManager.stop();
  }, [consented]);

  // Pick up server-side changes (role, consent, locale) when online.
  useEffect(() => {
    if (!session || !online) return;
    api
      .me()
      .then(updateUser)
      .catch((err) => err instanceof ApiError && err.status === 401 && expire());
  }, [session?.token, online, updateUser, expire]);

  useEffect(() => {
    getDb()
      .then((db) => Promise.all([db.getAll("queue"), db.getAll("observations")]))
      .then(([queue, observations]) => {
        setPending(queue.filter((i) => i.state !== "failed").length);
        setNeedsId(observations.filter((o) => o.status === "needs_id").length);
        setHasSightings(queue.length + observations.length > 0);
      });
  }, [version]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  async function authed(next: Session) {
    // Never upload one person's queued captures under another person's account.
    if (readLastUser() !== next.user.id) await clearUserData(await getDb());
    writeLastUser(next.user.id);
    setSession(next);
    if (isLocale(next.user.locale)) setLocale(next.user.locale);
    setView({ name: "capture" });
    syncManager.notify();
  }

  async function signOut() {
    if (pending > 0 && !confirm(t("signOutPending"))) return;
    syncManager.stop();
    await clearUserData(await getDb());
    expire();
  }

  function changeLocale(next: Locale) {
    setLocale(next);
    if (session) {
      updateUser({ ...session.user, locale: next });
      api.updateMe({ locale: next }).catch(() => {});
    }
  }

  const signedIn = !!session && consented;

  let content;
  if (!session) content = <AuthScreen onAuthed={authed} />;
  else if (!consented) content = <ConsentScreen onAccepted={updateUser} />;
  else if (view.name === "capture")
    content = (
      <CaptureScreen
        user={session.user}
        needsId={needsId}
        firstTime={!hasSightings}
        onOpenNeedsId={() => setView({ name: "sightings", filter: "needs_id" })}
        onSaved={(id) => setView(id ? { name: "detail", id } : { name: "sightings" })}
      />
    );
  else if (view.name === "sightings")
    content = (
      <SightingsScreen
        initialFilter={view.filter}
        onOpen={(id) => setView({ name: "detail", id })}
        onCapture={() => setView({ name: "capture" })}
      />
    );
  else if (view.name === "review")
    content = <ReviewScreen user={session.user} onUserChange={updateUser} />;
  else if (view.name === "me")
    content = (
      <ProgressScreen
        user={session.user}
        onUserChange={updateUser}
        locale={locale}
        onLocaleChange={changeLocale}
        onSignOut={signOut}
      />
    );
  else
    content = <ObservationDetail id={view.id} onBack={() => setView({ name: "sightings" })} />;

  const tab = (name: "capture" | "sightings" | "me" | "review", icon: ReactNode, label: string, badge = 0) => {
    const active = view.name === name || (name === "sightings" && view.name === "detail");
    return (
      <button
        className={active ? "active" : ""}
        aria-current={active ? "page" : undefined}
        onClick={() => setView({ name } as View)}
      >
        <span className="icon-wrap">
          {icon}
          {badge > 0 && <span className="badge" aria-label={t("needsIdBanner", { n: badge })}>{badge}</span>}
        </span>
        {label}
      </button>
    );
  };

  return (
    <LocaleContext.Provider value={locale}>
      <header className="topbar">
        <span className="brandmark">
          <img src="/icon.svg" alt="" />
          {t("appName")}
        </span>
        {!online ? (
          <span className="sync offline" role="status">
            <CloudOff size={14} aria-hidden /> {t("offline")}
          </span>
        ) : pending > 0 ? (
          <span className="sync pending" role="status">
            <UploadCloud size={14} aria-hidden /> {t("syncing", { n: pending })}
          </span>
        ) : signedIn ? (
          <span className="sync" role="status">
            <span className="dot" aria-hidden /> {t("online")}
          </span>
        ) : null}
        {!signedIn && (
          <select
            aria-label={t("language")}
            value={locale}
            onChange={(e) => isLocale(e.target.value) && changeLocale(e.target.value)}
          >
            {Object.entries(LOCALES).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        )}
        {session && !consented && (
          <button className="link small" onClick={signOut}>
            {t("signOut")}
          </button>
        )}
      </header>
      {content}
      {session && consented && (
        <nav className="tabs" aria-label={t("appName")}>
          {tab("capture", <Camera size={22} aria-hidden />, t("tabCapture"))}
          {tab("sightings", <Leaf size={22} aria-hidden />, t("tabSightings"), needsId)}
          {tab("me", <UserRound size={22} aria-hidden />, t("tabMe"))}
          {REVIEWER_ROLES.has(session.user.role) &&
            tab("review", <ShieldCheck size={22} aria-hidden />, t("tabReview"))}
        </nav>
      )}
    </LocaleContext.Provider>
  );
}
