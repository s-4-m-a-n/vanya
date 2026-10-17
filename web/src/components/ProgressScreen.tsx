import { ChevronRight, Languages, LayoutDashboard, LogOut, Medal, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api";
import { getPosition } from "../device";
import { dashboardApi } from "../dashboard/api";
import { isLocale, LOCALES, useT, type Locale, type MessageKey } from "../i18n";
import { useOnline } from "../syncManager";
import type { Leaderboard, Progress, User } from "../types";

const NEARBY_RADIUS_KM = 100;
const BADGE_ICONS: Record<string, string> = {
  first_sighting: "🌱", species_10: "🔟", species_50: "🌿", species_100: "🌳", birds_10: "🐦",
  animals_10: "🦊", plants_10: "🌸", rare_find: "💎", threatened_witness: "🛡️",
  explorer_5: "🧭", days_7: "📅", trusted_observer: "⭐",
};

export function levelTitle(t: ReturnType<typeof useT>, level: number): string {
  return t.dynamic(`level_${Math.min(level, 10)}`, "level_10");
}

interface Props {
  user: User;
  onUserChange: (u: User) => void;
  locale: Locale;
  onLocaleChange: (locale: Locale) => void;
  onSignOut: () => void;
}

function initials(name: string): string {
  const parts = name.replace(/\(.*?\)/g, "").trim().split(/\s+/);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}

export function ProgressScreen({ user, onUserChange, locale, onLocaleChange, onSignOut }: Props) {
  const t = useT();
  const [orgCount, setOrgCount] = useState(0);
  const online = useOnline();
  const [progress, setProgress] = useState<Progress | null>(null);
  const [period, setPeriod] = useState<Leaderboard["period"]>("week");
  const [nearby, setNearby] = useState(false);
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!online) return;
    dashboardApi.orgs().then((orgs) => setOrgCount(orgs.length)).catch(() => {});
  }, [online]);

  useEffect(() => {
    if (online) api.progress().then(setProgress).catch(() => setError(t("err_generic")));
    // `t` is a new function each render; reload only when connectivity changes.
  }, [online]);

  useEffect(() => {
    if (!online) return;
    let cancelled = false;
    (async () => {
      setError(null);
      let near;
      if (nearby) {
        const fix = await getPosition();
        if (!fix) {
          if (!cancelled) setError(t("nearbyNeedsLocation"));
          return;
        }
        near = { latitude: fix.latitude, longitude: fix.longitude, radiusKm: NEARBY_RADIUS_KM };
      }
      try {
        const result = await api.leaderboard({ period, near });
        if (!cancelled) setBoard(result);
      } catch {
        if (!cancelled) setError(t("err_generic"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [online, period, nearby]);

  async function toggleVisible(visible: boolean) {
    onUserChange(await api.updateMe({ leaderboard_visible: visible }));
    setProgress((p) => (p ? { ...p, leaderboard_visible: visible } : p));
    setBoard(await api.leaderboard({ period }));
  }

  const settings = (
    <>
      <section className="card settings">
        <label className="setting">
          <Languages size={20} aria-hidden />
          <span className="grow">{t("language")}</span>
          <select value={locale} onChange={(e) => isLocale(e.target.value) && onLocaleChange(e.target.value)}>
            {Object.entries(LOCALES).map(([code, label]) => (
              <option key={code} value={code}>{label}</option>
            ))}
          </select>
        </label>
        {progress && (
          <label className="setting">
            <Medal size={20} aria-hidden />
            <span className="grow">{t("showOnLeaderboard")}</span>
            <input
              type="checkbox"
              className="switch"
              checked={progress.leaderboard_visible}
              onChange={(e) => toggleVisible(e.target.checked)}
            />
          </label>
        )}
        {orgCount > 0 && (
          <a className="setting" href="/dashboard">
            <LayoutDashboard size={20} aria-hidden />
            <span className="grow">{t("dashTitle")}</span>
            <ChevronRight size={18} aria-hidden />
          </a>
        )}
      </section>
      <button className="danger block" onClick={onSignOut}>
        <LogOut size={18} aria-hidden /> {t("signOut")}
      </button>
    </>
  );

  if (online && !progress && !error) return <main className="screen"><p className="muted">…</p></main>;
  if (!online || !progress) {
    return (
      <main className="screen stack">
        {!online ? (
          <p className="tip"><WifiOff size={18} aria-hidden />{t("searchOffline")}</p>
        ) : (
          <p className="error">{error}</p>
        )}
        <h2>{t("settingsTitle")}</h2>
        {settings}
      </main>
    );
  }

  const { level } = progress;
  const span = level.next_at - level.floor;
  const pct = Math.min(100, Math.round(((progress.xp - level.floor) / span) * 100));
  const earned = new Set(progress.badges.map((b) => b.code));

  return (
    <main className="screen stack">
      <section className="card stack">
        <div className="profile">
          <span className="avatar" aria-hidden>
            {initials(user.display_name)}
            <span className="level">{level.number}</span>
          </span>
          <div className="grow">
            <h2>{user.display_name}</h2>
            <p className="muted small">
              {t("levelN", { n: level.number })} · {levelTitle(t, level.number)}
            </p>
          </div>
          <strong className="xp confirmed">{progress.xp} XP</strong>
        </div>
        <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${pct}%` }} />
        </div>
        <p className="muted small">
          {t("xpToNext", { n: level.next_at - progress.xp, next: level.number + 1 })}
          {progress.pending_xp > 0 && ` · ${t("pendingXp", { n: progress.pending_xp })}`}
        </p>
        <div className="stats">
          <div><strong>{progress.verified}</strong><span>{t("statVerified")}</span></div>
          <div><strong>{progress.species}</strong><span>{t("statSpecies")}</span></div>
          <div><strong>{progress.rare_finds}</strong><span>{t("statRareFinds")}</span></div>
          <div><strong>{progress.reputation}</strong><span>{t("statReputation")}</span></div>
        </div>
        <p className="muted small">{t("rareBonusLeft", { n: progress.rarity_bonus_left_today })}</p>
      </section>

      <section className="card stack">
        <h2 className="section-title">
          {t("badgesTitle")} <span className="count">{earned.size}/{progress.all_badges.length}</span>
        </h2>
        <ul className="badges">
          {progress.all_badges.map((code) => (
            <li key={code} className={earned.has(code) ? "earned" : "locked"} title={t.dynamic(`badgeDesc_${code}`, "badgesTitle")}>
              <span className="icon" aria-hidden>{BADGE_ICONS[code] ?? "🏅"}</span>
              <span className="small">{t.dynamic(`badge_${code}`, "badgesTitle")}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card stack">
        <h2>{t("leaderboardTitle")}</h2>
        <div className="segmented">
          {(["week", "month", "all"] as const).map((p) => (
            <button key={p} className={period === p ? "active" : ""} onClick={() => setPeriod(p)}>
              {t(`period${p[0].toUpperCase()}${p.slice(1)}` as MessageKey)}
            </button>
          ))}
        </div>
        <div className="segmented">
          <button className={!nearby ? "active" : ""} onClick={() => setNearby(false)}>{t("scopeGlobal")}</button>
          <button className={nearby ? "active" : ""} onClick={() => setNearby(true)}>{t("scopeNearby")}</button>
        </div>
        {error && <p className="error small">{error}</p>}
        {board && board.entries.length === 0 && <p className="muted">{t("leaderboardEmpty")}</p>}
        {board && board.entries.length > 0 && (
          <ol className="board">
            {board.entries.map((e) => (
              <li key={e.user_id} className={e.is_me ? "me" : ""}>
                <span className="rank">{e.rank}</span>
                <span className="grow">{e.display_name} <span className="muted small">· {levelTitle(t, e.level)}</span></span>
                <strong>{e.xp}</strong>
              </li>
            ))}
          </ol>
        )}
        {board && (
          <p className="muted small">
            {board.me ? t("yourRank", { rank: board.me.rank, xp: board.me.xp }) : t("notRanked")}
          </p>
        )}
      </section>

      <h2>{t("settingsTitle")}</h2>
      {settings}
    </main>
  );
}
