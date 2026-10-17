import "./dashboard.css";

import type { FeatureCollection, Point, Polygon } from "geojson";
import { Bell, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, loadSession, saveSession, type Session } from "../api";
import { AuthScreen } from "../components/AuthScreen";
import { isLocale, LOCALES, LocaleContext, translate, type Locale, type MessageKey } from "../i18n";
import type { TaxonGroup } from "../types";
import { dashboardApi, GROUPS } from "./api";
import { AlertsPanel, AnalystPanel, ReportsPanel } from "./Insights";
import { Manage } from "./Manage";
import { MapPanel } from "./MapPanel";
import { ChangesPanel, KpiTiles, NewRecords, RecordList, taxonName, TopSpecies } from "./Panels";
import { TrendChart } from "./TrendChart";
import type { Changes, Filters, NewRecord, Org, RecordItem, Region, Summary, TrendPoint } from "./types";

interface Data {
  summary: Summary;
  points: FeatureCollection<Point, RecordItem>;
  grid: FeatureCollection<Polygon, { observations: number; species: number }>;
  trends: TrendPoint[];
  changes: Changes;
  rare: RecordItem[];
  invasive: RecordItem[];
  newRecords: NewRecord[];
}

const PERIODS: [number | null, MessageKey][] = [
  [30, "dashLast30"],
  [90, "dashLast90"],
  [365, "dashLast365"],
  [null, "dashAllTime"],
];

export default function DashboardApp() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [locale, setLocale] = useState<Locale>(() =>
    isLocale(session?.user.locale) ? session!.user.locale as Locale : "en",
  );
  const t = (key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars);

  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [orgId, setOrgId] = useState<string | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [filters, setFilters] = useState<Filters>({ regionId: null, group: null, taxon: null, days: 90 });
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);
  const [mapMode, setMapMode] = useState<"points" | "grid">("points");
  const [drawing, setDrawing] = useState(false);
  const [watchVersion, setWatchVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [unreadAlerts, setUnreadAlerts] = useState(0);

  const org = orgs?.find((o) => o.id === orgId) ?? null;

  const signOut = useCallback(() => {
    saveSession(null);
    setSession(null);
    setOrgs(null);
  }, []);

  useEffect(() => {
    if (!session) return;
    dashboardApi
      .orgs()
      .then((list) => {
        setOrgs(list);
        setOrgId((current) => current ?? list[0]?.id ?? null);
      })
      .catch((err) => (err instanceof ApiError && err.status === 401 ? signOut() : setError(t("err_generic"))));
  }, [session, signOut]); // `t` is rebuilt every render

  const loadRegions = useCallback(() => {
    if (orgId) dashboardApi.regions(orgId).then(setRegions).catch(() => setRegions([]));
  }, [orgId]);
  useEffect(loadRegions, [loadRegions]);

  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    setLoading(true);
    // Every panel reads the same filters, so the numbers always agree.
    Promise.all([
      dashboardApi.summary(orgId, filters),
      dashboardApi.points(orgId, filters),
      dashboardApi.grid(orgId, filters, 0.5),
      dashboardApi.trends(orgId, filters),
      dashboardApi.changes(orgId, filters),
      dashboardApi.rare(orgId, filters),
      dashboardApi.invasive(orgId, filters),
      dashboardApi.newRecords(orgId, filters),
    ])
      .then(([summary, points, grid, trends, changes, rare, invasive, newRecords]) => {
        if (!cancelled) setData({ summary, points, grid, trends, changes, rare, invasive, newRecords });
      })
      .catch(() => !cancelled && setError(t("err_generic")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [orgId, filters, regions, watchVersion]); // `t` is rebuilt every render

  async function saveDrawn(ring: [number, number][]) {
    if (!orgId) return;
    const name = prompt(t("dashRegionName"));
    setDrawing(false);
    if (!name) return;
    try {
      await dashboardApi.addRegion(orgId, name, { type: "Polygon", coordinates: [ring] });
      loadRegions();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("err_generic"));
    }
  }

  let body;
  if (!session) {
    body = <AuthScreen onAuthed={(s) => { setSession(s); if (isLocale(s.user.locale)) setLocale(s.user.locale); }} />;
  } else if (orgs && orgs.length === 0) {
    body = <main className="dash-main"><p className="dash-card">{t("dashNoOrg")}</p></main>;
  } else if (!org) {
    body = <main className="dash-main"><p className="dash-note">{t("dashLoading")}</p></main>;
  } else {
    body = (
      <>
        <div className="dash-filters">
          <select value={filters.days ?? ""} onChange={(e) => setFilters({ ...filters, days: e.target.value ? Number(e.target.value) : null })} aria-label={t("dashLast90")}>
            {PERIODS.map(([days, key]) => <option key={key} value={days ?? ""}>{t(key)}</option>)}
          </select>
          <select value={filters.regionId ?? ""} onChange={(e) => setFilters({ ...filters, regionId: e.target.value ? Number(e.target.value) : null })} aria-label={t("dashRegions")}>
            <option value="">{t("dashAllRegions")}</option>
            {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <select value={filters.group ?? ""} onChange={(e) => setFilters({ ...filters, group: (e.target.value || null) as TaxonGroup | null })} aria-label={t("dashAllGroups")}>
            <option value="">{t("dashAllGroups")}</option>
            {GROUPS.map((g) => <option key={g} value={g}>{t(`group_${g}_one`)}</option>)}
          </select>
          {filters.taxon && (
            <span className="chip">
              {taxonName(filters.taxon)}
              <button className="link small" aria-label={t("dashClearSpecies")} onClick={() => setFilters({ ...filters, taxon: null })}><X size={14} aria-hidden /></button>
            </span>
          )}
        </div>
        {error && <p className="error small" style={{ padding: "0 1.25rem" }}>{error}</p>}
        <main className={`dash-main ${loading && data ? "refetching" : ""}`}>
          {data ? (
            <>
              <KpiTiles summary={data.summary} />
              <div className="dash-row even">
                <AlertsPanel org={org} onUnread={setUnreadAlerts} />
                <AnalystPanel org={org} />
              </div>
              <div className="dash-row">
                <section className="dash-card">
                  <div className="row center" style={{ marginBottom: "0.5rem" }}>
                    <h2 className="grow" style={{ margin: 0 }}>{t("dashMap")}</h2>
                    <div className="segmented" style={{ minWidth: 220 }}>
                      <button className={mapMode === "points" ? "active" : ""} onClick={() => setMapMode("points")}>{t("dashPoints")}</button>
                      <button className={mapMode === "grid" ? "active" : ""} onClick={() => setMapMode("grid")}>{t("dashGrid")}</button>
                    </div>
                  </div>
                  {mapMode === "points" && (
                    <div className="dash-legend">
                      {GROUPS.map((g) => <span key={g}><span className={`key key-${g}`} />{t(`group_${g}_one`)}</span>)}
                      <span><span className="key ring" />{t("protectedSpecies")}</span>
                    </div>
                  )}
                  <MapPanel
                    regions={regions}
                    fitKey={org.id}
                    points={data.points}
                    grid={data.grid}
                    mode={mapMode}
                    drawing={drawing}
                    onDrawn={saveDrawn}
                    onCancelDraw={() => setDrawing(false)}
                  />
                </section>
                <div className="dash-stack">
                  <section className="dash-card">
                    <h2>{t("dashTopSpecies")}</h2>
                    <TopSpecies summary={data.summary} onPick={(taxon) => setFilters({ ...filters, taxon })} />
                  </section>
                  <section className="dash-card">
                    <h2>{t("dashRarePanel")}</h2>
                    <RecordList items={data.rare} empty={t("dashNone")} />
                  </section>
                </div>
              </div>
              <div className="dash-row even">
                <section className="dash-card">
                  <h2>{t("dashTrends")}</h2>
                  <TrendChart data={data.trends} />
                </section>
                <section className="dash-card">
                  <h2>{t("dashChanges")}</h2>
                  <ChangesPanel changes={data.changes} />
                </section>
              </div>
              <div className="dash-row even">
                <section className="dash-card">
                  <h2>{t("dashInvasivePanel")}</h2>
                  <RecordList items={data.invasive} empty={t("dashNoInvasiveList")} />
                </section>
                <section className="dash-card">
                  <h2>{t("dashNewPanel")}</h2>
                  <NewRecords items={data.newRecords} />
                </section>
              </div>
              <ReportsPanel org={org} />
              <Manage
                org={org}
                regions={regions}
                onRegionsChanged={loadRegions}
                onWatchlistChanged={() => setWatchVersion((v) => v + 1)}
                onStartDrawing={() => {
                  setDrawing(true);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              />
            </>
          ) : (
            <p className="dash-note">{t("dashLoading")}</p>
          )}
        </main>
      </>
    );
  }

  return (
    <LocaleContext.Provider value={locale}>
      <div className="dash">
        <header className="dash-header">
          <a className="dash-brand" href="/dashboard">
            <img src="/icon.svg" alt="" />
            {t("appName")}
          </a>
          <h1>{t("dashTitle")}</h1>
          {orgs && orgs.length > 1 && (
            <select value={orgId ?? ""} onChange={(e) => setOrgId(e.target.value)} aria-label={t("dashOrganisation")}>
              {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          )}
          {orgs?.length === 1 && org && <strong>{org.name}</strong>}
          {org && unreadAlerts > 0 && (
            <span className="chip alert-badge" title={t("alertsTitle")}><Bell size={12} aria-hidden /> {t("alertsUnread", { n: unreadAlerts })}</span>
          )}
          <select value={locale} onChange={(e) => isLocale(e.target.value) && setLocale(e.target.value)} aria-label={t("language")}>
            {Object.entries(LOCALES).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
          <a href="/" className="small dash-link">{t("dashOpenApp")}</a>
          {session && <button className="link small" onClick={signOut}>{t("signOut")}</button>}
        </header>
        {body}
      </div>
    </LocaleContext.Provider>
  );
}
