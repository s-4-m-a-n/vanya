import "./dashboard.css";

import type { FeatureCollection, Point, Polygon } from "geojson";
import {
  Bell, Building2, FileText, Leaf, LayoutDashboard, Map as MapIcon, Settings, Sparkles, X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, loadSession, saveSession, type Session } from "../api";
import { ConsoleMessage, ConsoleShell, ConsoleSignIn, type NavItem } from "../console/ConsoleShell";
import { isLocale, LocaleContext, translate, type Locale, type MessageKey } from "../i18n";
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

type Page = "overview" | "species" | "alerts" | "analyst" | "reports" | "settings";
const PAGES: Page[] = ["overview", "species", "alerts", "analyst", "reports", "settings"];

const PERIODS: [number | null, MessageKey][] = [
  [30, "dashLast30"],
  [90, "dashLast90"],
  [365, "dashLast365"],
  [null, "dashAllTime"],
];

function pageFromHash(): Page {
  const hash = window.location.hash.slice(1) as Page;
  return PAGES.includes(hash) ? hash : "overview";
}

export default function DashboardApp() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [locale, setLocale] = useState<Locale>(() =>
    isLocale(session?.user.locale) ? session!.user.locale as Locale : "en",
  );
  const t = (key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars);

  const [page, setPageState] = useState<Page>(pageFromHash);
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [orgId, setOrgId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("org"));
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

  function setPage(next: Page) {
    setPageState(next);
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${next}`);
    window.scrollTo({ top: 0 });
  }

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
        setOrgId((current) => (current && list.some((o) => o.id === current) ? current : list[0]?.id ?? null));
      })
      .catch((err) => (err instanceof ApiError && err.status === 401 ? signOut() : setError(t("err_generic"))));
  }, [session, signOut]); // `t` is rebuilt every render

  const loadRegions = useCallback(() => {
    if (orgId) dashboardApi.regions(orgId).then(setRegions).catch(() => setRegions([]));
  }, [orgId]);
  useEffect(loadRegions, [loadRegions]);

  // Unread count for the sidebar badge, wherever the user is in the dashboard.
  useEffect(() => {
    if (!orgId) return;
    const poll = () => dashboardApi.alerts(orgId, true).then((r) => setUnreadAlerts(r.unread)).catch(() => {});
    void poll();
    const timer = setInterval(poll, 30_000);
    return () => clearInterval(timer);
  }, [orgId]);

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

  if (!session) {
    return (
      <LocaleContext.Provider value={locale}>
        <ConsoleSignIn
          product={t("dashTitle")}
          lead={t("consoleOrgLead")}
          points={[
            { icon: <MapIcon size={20} />, text: t("consoleOrg1") },
            { icon: <Bell size={20} />, text: t("consoleOrg2") },
            { icon: <Sparkles size={20} />, text: t("consoleOrg3") },
          ]}
          locale={locale}
          onLocale={setLocale}
          onAuthed={(s) => {
            setSession(s);
            if (isLocale(s.user.locale)) setLocale(s.user.locale);
          }}
        />
      </LocaleContext.Provider>
    );
  }

  const nav: NavItem<Page>[] = org
    ? [
        { key: "overview", label: t("navOverview"), icon: <LayoutDashboard size={18} /> },
        { key: "species", label: t("navSpecies"), icon: <Leaf size={18} /> },
        { key: "alerts", label: t("alertsTitle"), icon: <Bell size={18} />, badge: unreadAlerts },
        { key: "analyst", label: t("navAnalyst"), icon: <Sparkles size={18} /> },
        { key: "reports", label: t("reportsTitle"), icon: <FileText size={18} /> },
        { key: "settings", label: t("settingsTitle"), icon: <Settings size={18} /> },
      ]
    : [];

  const titles: Record<Page, MessageKey> = {
    overview: "navOverview", species: "navSpecies", alerts: "alertsTitle",
    analyst: "analystTitle", reports: "reportsTitle", settings: "settingsTitle",
  };

  const filterBar = (
    <div className="console-filters">
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
  );

  let body;
  if (orgs && orgs.length === 0) {
    body = <ConsoleMessage icon={<Building2 size={30} />}>{t("dashNoOrg")}</ConsoleMessage>;
  } else if (!org) {
    body = <p className="dash-note" style={{ padding: "1.5rem" }}>{t("dashLoading")}</p>;
  } else if (page === "alerts") {
    body = <main className="dash-main"><AlertsPanel org={org} onUnread={setUnreadAlerts} /></main>;
  } else if (page === "analyst") {
    body = <main className="dash-main"><AnalystPanel org={org} /></main>;
  } else if (page === "reports") {
    body = <main className="dash-main"><ReportsPanel org={org} /></main>;
  } else if (page === "settings") {
    body = (
      <main className="dash-main">
        <Manage
          org={org}
          regions={regions}
          onRegionsChanged={loadRegions}
          onWatchlistChanged={() => setWatchVersion((v) => v + 1)}
          onStartDrawing={() => {
            setMapMode("points");
            setDrawing(true);
            setPage("overview");
          }}
        />
      </main>
    );
  } else if (!data) {
    body = <p className="dash-note" style={{ padding: "1.5rem" }}>{t("dashLoading")}</p>;
  } else if (page === "species") {
    body = (
      <main className={`dash-main ${loading ? "refetching" : ""}`}>
        <div className="dash-row even">
          <section className="dash-card">
            <h2>{t("dashRarePanel")}</h2>
            <RecordList items={data.rare} empty={t("dashNone")} />
          </section>
          <section className="dash-card">
            <h2>{t("dashTopSpecies")}</h2>
            <TopSpecies summary={data.summary} onPick={(taxon) => setFilters({ ...filters, taxon })} />
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
        <section className="dash-card">
          <h2>{t("dashChanges")}</h2>
          <ChangesPanel changes={data.changes} />
        </section>
      </main>
    );
  } else {
    body = (
      <main className={`dash-main ${loading ? "refetching" : ""}`}>
        <KpiTiles summary={data.summary} />
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
        <section className="dash-card">
          <h2>{t("dashTrends")}</h2>
          <TrendChart data={data.trends} />
        </section>
      </main>
    );
  }

  const orgSwitcher = orgs && orgs.length > 0 && (
    <div>
      <div className="console-org-label">{t("dashOrganisation")}</div>
      {orgs.length > 1 ? (
        <select value={orgId ?? ""} onChange={(e) => setOrgId(e.target.value)} aria-label={t("dashOrganisation")}>
          {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      ) : (
        <strong style={{ padding: "0 0.35rem" }}>{orgs[0].name}</strong>
      )}
    </div>
  );

  return (
    <LocaleContext.Provider value={locale}>
      <ConsoleShell
        product={t("dashTitle")}
        nav={nav}
        active={page}
        onNavigate={setPage}
        sidebarTop={orgSwitcher}
        session={session}
        locale={locale}
        onLocale={setLocale}
        onSignOut={signOut}
        links={[
          { href: "/", label: t("dashOpenApp") },
          ...(session.user.role === "admin" ? [{ href: "/admin", label: t("consoleOpenAdmin") }] : []),
        ]}
        title={org ? t(titles[page]) : t("dashTitle")}
        subtitle={org ? `${org.name}${error ? ` · ${error}` : ""}` : undefined}
        actions={org && (page === "overview" || page === "species") ? filterBar : undefined}
      >
        {body}
      </ConsoleShell>
    </LocaleContext.Provider>
  );
}
