import "../dashboard/dashboard.css";

import {
  Activity, Building2, Eye, LayoutDashboard, Lock, MapPinOff, ShieldAlert, ShieldCheck, Users,
} from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, loadSession, saveSession, type Session } from "../api";
import { ConsoleMessage, ConsoleShell, ConsoleSignIn, type NavItem } from "../console/ConsoleShell";
import { isLocale, LocaleContext, translate, useT, type Locale, type MessageKey } from "../i18n";
import type { Role } from "../types";
import {
  adminApi, ORG_KINDS, type AccessEntry, type AdminOrg, type AdminTaxon, type AdminUser,
  type Overview, type RestrictedArea, type Sensitivity,
} from "./api";

type Page = "overview" | "users" | "orgs" | "protection" | "areas" | "access";
const PAGES: Page[] = ["overview", "users", "orgs", "protection", "areas", "access"];
const ROLES: Role[] = ["observer", "verifier", "analyst", "admin"];
const PAGE_SIZE = 25;

function pageFromHash(): Page {
  const hash = window.location.hash.slice(1) as Page;
  return PAGES.includes(hash) ? hash : "overview";
}

function useErrorText() {
  const t = useT();
  return (err: unknown) => (err instanceof ApiError ? err.message : t("err_generic"));
}

export default function AdminApp() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [locale, setLocale] = useState<Locale>(() =>
    isLocale(session?.user.locale) ? session!.user.locale as Locale : "en",
  );
  const [page, setPageState] = useState<Page>(pageFromHash);
  const t = (key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars);

  function setPage(next: Page) {
    setPageState(next);
    history.replaceState(null, "", `${window.location.pathname}#${next}`);
    window.scrollTo({ top: 0 });
  }

  const signOut = useCallback(() => {
    saveSession(null);
    setSession(null);
  }, []);

  if (!session) {
    return (
      <LocaleContext.Provider value={locale}>
        <ConsoleSignIn
          product={t("adminTitle")}
          lead={t("consoleAdminLead")}
          points={[
            { icon: <Users size={20} />, text: t("consoleAdmin1") },
            { icon: <ShieldCheck size={20} />, text: t("consoleAdmin2") },
            { icon: <Eye size={20} />, text: t("consoleAdmin3") },
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

  const isAdmin = session.user.role === "admin";
  const nav: NavItem<Page>[] = isAdmin
    ? [
        { key: "overview", label: t("navOverview"), icon: <LayoutDashboard size={18} /> },
        { key: "users", label: t("navUsers"), icon: <Users size={18} /> },
        { key: "orgs", label: t("navOrgs"), icon: <Building2 size={18} /> },
        { key: "protection", label: t("navProtection"), icon: <ShieldCheck size={18} /> },
        { key: "areas", label: t("navRestricted"), icon: <MapPinOff size={18} /> },
        { key: "access", label: t("navAccessLog"), icon: <Eye size={18} /> },
      ]
    : [];
  const titles: Record<Page, MessageKey> = {
    overview: "navOverview", users: "navUsers", orgs: "navOrgs",
    protection: "navProtection", areas: "navRestricted", access: "navAccessLog",
  };

  let body;
  if (!isAdmin) body = <ConsoleMessage icon={<Lock size={30} />}>{t("adminOnly")}</ConsoleMessage>;
  else if (page === "users") body = <UsersPage me={session.user.id} />;
  else if (page === "orgs") body = <OrgsPage />;
  else if (page === "protection") body = <ProtectionPage />;
  else if (page === "areas") body = <AreasPage />;
  else if (page === "access") body = <AccessPage />;
  else body = <OverviewPage />;

  return (
    <LocaleContext.Provider value={locale}>
      <ConsoleShell
        product={t("adminTitle")}
        nav={nav}
        active={page}
        onNavigate={setPage}
        session={session}
        locale={locale}
        onLocale={setLocale}
        onSignOut={signOut}
        links={[
          { href: "/", label: t("dashOpenApp") },
          { href: "/dashboard", label: t("dashTitle") },
        ]}
        title={isAdmin ? t(titles[page]) : t("adminTitle")}
      >
        <main className="dash-main">{body}</main>
      </ConsoleShell>
    </LocaleContext.Provider>
  );
}

// --- Overview ----------------------------------------------------------------------------

function OverviewPage() {
  const t = useT();
  const errorText = useErrorText();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .overview()
      .then(setData)
      .catch((err) => setError(errorText(err)));
  }, []); // load once per visit

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="dash-note">{t("dashLoading")}</p>;

  const tiles: [MessageKey, number, string?][] = [
    ["navUsers", data.users, t("adminNewUsers", { n: data.new_users_7d })],
    ["adminAwaiting", data.awaiting_review],
    ["adminVerified7", data.verified_7d],
    ["navOrgs", data.organisations],
    ["adminProtected", data.protected_species],
    ["adminReveals", data.reveals_7d],
  ];
  const statuses = Object.entries(data.observations_by_status).sort((a, b) => b[1] - a[1]);
  const maxStatus = Math.max(1, ...statuses.map(([, n]) => n));

  return (
    <>
      <div className="dash-tiles">
        {tiles.map(([key, value, note]) => (
          <div key={key} className="dash-tile">
            <div className="label">{t(key)}</div>
            <div className="value">{value.toLocaleString(t.locale)}</div>
            {note && <div className="dash-note">{note}</div>}
          </div>
        ))}
      </div>
      <section className="dash-card">
        <h2 className="dash-h2"><Activity size={18} aria-hidden /> {t("adminActivity")}</h2>
        <ActivityChart daily={data.daily} />
      </section>
      <div className="dash-row even">
        <section className="dash-card">
          <h2>{t("adminByStatus")}</h2>
          <ul className="bar-list">
            {statuses.map(([status, n]) => (
              <li key={status}>
                <span className="grow">{t.dynamic(`status_${status}`, "status_submitted")}</span>
                <span className="bar-track"><span style={{ width: `${(n / maxStatus) * 100}%` }} /></span>
                <strong>{n}</strong>
              </li>
            ))}
            {statuses.length === 0 && <li className="dash-note">{t("adminNone")}</li>}
          </ul>
        </section>
        <section className="dash-card">
          <h2>{t("adminTopObservers")}</h2>
          {data.top_observers.length === 0 ? (
            <p className="dash-note">{t("adminNone")}</p>
          ) : (
            <table className="dash-table">
              <tbody>
                {data.top_observers.map((o, i) => (
                  <tr key={o.user_id}>
                    <td style={{ width: "2rem" }} className="dash-note">{i + 1}</td>
                    <td>{o.display_name}</td>
                    <td className="num">{o.verified}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h2 style={{ marginTop: "1rem" }}>{t("adminByRole")}</h2>
          <div className="row">
            {ROLES.map((r) => (
              <span key={r} className="chip">{t(`role_${r}`)}: {data.users_by_role[r] ?? 0}</span>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}

/** Grouped daily bars: submitted (blue) and verified (green), with a hover readout. */
function ActivityChart({ daily }: { daily: Overview["daily"] }) {
  const t = useT();
  const [hover, setHover] = useState<number | null>(null);
  const W = 900, H = 220, PAD_L = 32, PAD_B = 26, PAD_T = 8;
  const max = Math.max(4, ...daily.map((d) => Math.max(d.submitted, d.verified)));
  const step = (W - PAD_L) / daily.length;
  const bar = Math.max(2, step / 2 - 2);
  const y = (v: number) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / max);
  const ticks = [0, Math.round(max / 2), max];
  const fmt = (d: string) => new Date(d).toLocaleDateString(t.locale, { day: "numeric", month: "short" });
  const current = hover != null ? daily[hover] : null;

  return (
    <div className="trend">
      <div className="dash-legend">
        <span><span className="key key-bird rect" />{t("adminSubmitted")}</span>
        <span><span className="key key-plant rect" />{t("adminVerifiedSeries")}</span>
        {current && <span className="dash-note">{fmt(current.day)}: {current.submitted} / {current.verified}</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("adminActivity")}>
        <g className="grid">
          {ticks.map((v) => <line key={v} x1={PAD_L} x2={W} y1={y(v)} y2={y(v)} />)}
        </g>
        <g className="axis">
          {ticks.map((v) => <text key={v} x={PAD_L - 6} y={y(v) + 4} textAnchor="end">{v}</text>)}
          {daily.map((d, i) => i % 5 === 0 && (
            <text key={d.day} x={PAD_L + step * i + step / 2} y={H - 6} textAnchor="middle">{fmt(d.day)}</text>
          ))}
        </g>
        {daily.map((d, i) => {
          const x = PAD_L + step * i + step / 2;
          return (
            <g key={d.day} className="col" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect className="hit" x={x - step / 2} y={PAD_T} width={step} height={H - PAD_T - PAD_B} />
              {d.submitted > 0 && <rect className="seg seg-bird" x={x - bar - 1} y={y(d.submitted)} width={bar} height={y(0) - y(d.submitted)} rx={2} />}
              {d.verified > 0 && <rect className="seg seg-plant" x={x + 1} y={y(d.verified)} width={bar} height={y(0) - y(d.verified)} rx={2} />}
            </g>
          );
        })}
        <line className="baseline" x1={PAD_L} x2={W} y1={y(0)} y2={y(0)} />
      </svg>
    </div>
  );
}

// --- Users -------------------------------------------------------------------------------

function UsersPage({ me }: { me: string }) {
  const t = useT();
  const errorText = useErrorText();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ items: AdminUser[]; total: number } | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      adminApi.users({ q: q.trim(), role, limit: PAGE_SIZE, offset }).then(setPage).catch((e) => setMessage(errorText(e)));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, role, offset]);

  async function changeRole(user: AdminUser, next: Role) {
    setMessage(null);
    try {
      await adminApi.setRole(user.id, next);
      setPage((p) => p && { ...p, items: p.items.map((u) => (u.id === user.id ? { ...u, role: next } : u)) });
      setMessage(`${user.display_name}: ${t(`role_${next}`)} · ${t("adminSaved")}`);
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  const date = (d: string | null) => (d ? new Date(d).toLocaleDateString(t.locale) : "—");
  return (
    <section className="dash-card">
      <div className="console-toolbar">
        <input type="search" placeholder={t("adminSearchUsers")} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select value={role} onChange={(e) => { setRole(e.target.value); setOffset(0); }} aria-label={t("colRole")}>
          <option value="">{t("adminAllRoles")}</option>
          {ROLES.map((r) => <option key={r} value={r}>{t(`role_${r}`)}</option>)}
        </select>
        {message && <span className="dash-note">{message}</span>}
      </div>
      <div className="console-table-wrap">
        <table className="console-table">
          <thead>
            <tr>
              <th>{t("colName")}</th><th>{t("colRole")}</th><th className="num">{t("colSightings")}</th>
              <th className="num">{t("filterVerified")}</th><th className="num">{t("statReputation")}</th>
              <th>{t("colLast")}</th><th>{t("colJoined")}</th>
            </tr>
          </thead>
          <tbody>
            {page?.items.map((u) => (
              <tr key={u.id}>
                <td><strong>{u.display_name}</strong><span className="sub">{u.email}</span></td>
                <td>
                  <select value={u.role} disabled={u.id === me} onChange={(e) => changeRole(u, e.target.value as Role)} aria-label={t("colRole")}>
                    {ROLES.map((r) => <option key={r} value={r}>{t(`role_${r}`)}</option>)}
                  </select>
                </td>
                <td className="num">{u.observations}</td>
                <td className="num">{u.verified}</td>
                <td className="num">{u.reputation}</td>
                <td>{date(u.last_sighting_at)}</td>
                <td>{date(u.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {page && page.total === 0 && <p className="dash-note">{t("adminNone")}</p>}
      {page && page.total > 0 && (
        <div className="console-pager">
          <span className="dash-note">
            {t("adminShowing", { from: offset + 1, to: Math.min(offset + PAGE_SIZE, page.total), total: page.total })}
          </span>
          <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>{t("adminPrev")}</button>
          <button disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>{t("adminNext")}</button>
        </div>
      )}
    </section>
  );
}

// --- Organisations -------------------------------------------------------------------------

function OrgsPage() {
  const t = useT();
  const errorText = useErrorText();
  const [orgs, setOrgs] = useState<AdminOrg[] | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>("ngo");
  const [memberFor, setMemberFor] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"admin" | "analyst">("admin");
  const [message, setMessage] = useState<string | null>(null);

  const load = () => adminApi.orgs().then(setOrgs).catch((e) => setMessage(errorText(e)));
  useEffect(() => void load(), []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      await adminApi.createOrg(name.trim(), kind);
      setName("");
      setMessage(t("adminSaved"));
      void load();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  async function addMember(event: FormEvent, org: AdminOrg) {
    event.preventDefault();
    setMessage(null);
    try {
      await adminApi.addMember(org.id, email.trim(), memberRole);
      setEmail("");
      setMemberFor(null);
      setMessage(t("adminSaved"));
      void load();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <>
      <section className="dash-card">
        <h2>{t("adminCreateOrg")}</h2>
        <form className="console-toolbar" onSubmit={create}>
          <input type="text" required maxLength={200} placeholder={t("adminOrgName")} value={name} onChange={(e) => setName(e.target.value)} />
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label={t("colKind")}>
            {ORG_KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
          </select>
          <button className="primary">{t("adminCreateOrg")}</button>
          {message && <span className="dash-note">{message}</span>}
        </form>
      </section>
      <section className="dash-card">
        <div className="console-table-wrap">
          <table className="console-table">
            <thead>
              <tr>
                <th>{t("colName")}</th><th>{t("colKind")}</th><th className="num">{t("dashMembers")}</th>
                <th className="num">{t("dashRegions")}</th><th>{t("colAdmins")}</th><th />
              </tr>
            </thead>
            <tbody>
              {orgs?.map((o) => (
                <tr key={o.id}>
                  <td><strong>{o.name}</strong><span className="sub">{new Date(o.created_at).toLocaleDateString(t.locale)}</span></td>
                  <td>{t.dynamic(`kind_${o.kind}`, "kind_other")}</td>
                  <td className="num">{o.members}</td>
                  <td className="num">{o.regions}</td>
                  <td>
                    {o.admins.join(", ") || "—"}
                    {memberFor === o.id && (
                      <form className="console-toolbar" style={{ marginTop: "0.5rem", marginBottom: 0 }} onSubmit={(e) => addMember(e, o)}>
                        <input type="email" required placeholder={t("adminMemberEmail")} value={email} onChange={(e) => setEmail(e.target.value)} />
                        <select value={memberRole} onChange={(e) => setMemberRole(e.target.value as "admin" | "analyst")} aria-label={t("colRole")}>
                          <option value="admin">{t("dashRole_admin")}</option>
                          <option value="analyst">{t("dashRole_analyst")}</option>
                        </select>
                        <button className="primary">{t("dashAddMember")}</button>
                      </form>
                    )}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="link small" onClick={() => setMemberFor(memberFor === o.id ? null : o.id)}>{t("dashAddMember")}</button>
                    {" · "}
                    <a className="small" href={`/dashboard?org=${o.id}`}>{t("adminOpenDashboard")}</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {orgs?.length === 0 && <p className="dash-note">{t("adminNone")}</p>}
      </section>
    </>
  );
}

// --- Species protection ---------------------------------------------------------------------

function ProtectionPage() {
  const t = useT();
  const errorText = useErrorText();
  const [q, setQ] = useState("");
  const [protectedOnly, setProtectedOnly] = useState(true);
  const [rows, setRows] = useState<AdminTaxon[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => adminApi.taxa(q.trim(), protectedOnly).then(setRows).catch((e) => setMessage(errorText(e)));
  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [q, protectedOnly]);

  async function change(row: AdminTaxon, value: string) {
    setMessage(null);
    try {
      await adminApi.setSensitivity(row.taxon.id, value ? (value as Sensitivity) : null);
      setMessage(`${row.taxon.common_name ?? row.taxon.scientific_name} · ${t("adminSaved")}`);
      void load();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <section className="dash-card">
      <p className="dash-note" style={{ marginBottom: "0.75rem" }}>{t("adminProtectionHint")}</p>
      <div className="console-toolbar">
        <input type="search" placeholder={t("searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={protectedOnly} onChange={(e) => setProtectedOnly(e.target.checked)} />
          {t("adminProtectedOnly")}
        </label>
        {message && <span className="dash-note">{message}</span>}
      </div>
      <div className="console-table-wrap">
        <table className="console-table">
          <thead>
            <tr>
              <th>{t("dashSpecies")}</th><th>{t("colIucn")}</th><th>{t("colSource")}</th>
              <th className="num">{t("colSightings")}</th><th>{t("colPublicLocation")}</th>
            </tr>
          </thead>
          <tbody>
            {rows?.map((row) => (
              <tr key={row.taxon.id}>
                <td>
                  <strong>{row.taxon.common_name ?? row.taxon.scientific_name}</strong>
                  <span className="sub"><em>{row.taxon.scientific_name}</em></span>
                </td>
                <td>{t.dynamic(`iucn_${row.taxon.iucn_status}`, "iucn_NE")}</td>
                <td>{row.status_source ? t.dynamic(`source_${row.status_source}`, "adminNone") : "—"}</td>
                <td className="num">{row.observations}</td>
                <td>
                  <select value={row.sensitivity ?? ""} onChange={(e) => change(row, e.target.value)} aria-label={t("colPublicLocation")}>
                    <option value="">{t("protection_auto")}</option>
                    <option value="open">{t("protection_open")}</option>
                    <option value="obscured">{t("protection_obscured")}</option>
                    <option value="hidden">{t("protection_hidden")}</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows?.length === 0 && <p className="dash-note">{t("adminNone")}</p>}
    </section>
  );
}

// --- Restricted areas -----------------------------------------------------------------------

function AreasPage() {
  const t = useT();
  const errorText = useErrorText();
  const [areas, setAreas] = useState<RestrictedArea[] | null>(null);
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => adminApi.areas().then(setAreas).catch((e) => setMessage(errorText(e)));
  useEffect(() => void load(), []);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setMessage(null);
    try {
      const geojson = JSON.parse(await file.text());
      await adminApi.addArea(name.trim(), reason.trim(), geojson);
      setName("");
      setReason("");
      setFile(null);
      (event.target as HTMLFormElement).reset();
      setMessage(t("adminSaved"));
      void load();
    } catch (err) {
      setMessage(err instanceof SyntaxError ? "GeoJSON: " + err.message : errorText(err));
    }
  }

  async function toggle(area: RestrictedArea) {
    try {
      const updated = await adminApi.setAreaActive(area.id, !area.active);
      setAreas((list) => list && list.map((a) => (a.id === area.id ? updated : a)));
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <>
      <section className="dash-card">
        <h2 className="dash-h2"><ShieldAlert size={18} aria-hidden /> {t("adminAddArea")}</h2>
        <p className="dash-note" style={{ marginBottom: "0.75rem" }}>{t("adminAreaHint")}</p>
        <form className="console-toolbar" onSubmit={add}>
          <input type="text" required maxLength={200} placeholder={t("colName")} value={name} onChange={(e) => setName(e.target.value)} />
          <input type="text" maxLength={300} placeholder={t("adminAreaReason")} value={reason} onChange={(e) => setReason(e.target.value)} />
          <input type="file" accept=".geojson,.json,application/geo+json,application/json" required aria-label={t("adminChooseGeojson")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ width: "auto" }} />
          <button className="primary" disabled={!file}>{t("adminAddArea")}</button>
          {message && <span className="dash-note">{message}</span>}
        </form>
      </section>
      <section className="dash-card">
        <div className="console-table-wrap">
          <table className="console-table">
            <thead>
              <tr><th>{t("colName")}</th><th>{t("adminAreaReason")}</th><th className="num">km²</th><th>{t("colActive")}</th></tr>
            </thead>
            <tbody>
              {areas?.map((a) => (
                <tr key={a.id}>
                  <td><strong>{a.name}</strong><span className="sub">{new Date(a.created_at).toLocaleDateString(t.locale)}</span></td>
                  <td>{a.reason ?? "—"}</td>
                  <td className="num">{a.area_km2.toLocaleString(t.locale)}</td>
                  <td><input type="checkbox" className="switch" checked={a.active} onChange={() => toggle(a)} aria-label={t("colActive")} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {areas?.length === 0 && <p className="dash-note">{t("adminNone")}</p>}
      </section>
    </>
  );
}

// --- Location access log ----------------------------------------------------------------------

function AccessPage() {
  const t = useT();
  const errorText = useErrorText();
  const [rows, setRows] = useState<AccessEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    adminApi.accessLog().then(setRows).catch((e) => setError(errorText(e)));
  }, []);

  return (
    <section className="dash-card">
      {error && <p className="error">{error}</p>}
      {rows?.length === 0 ? (
        <p className="dash-note">{t("adminLogEmpty")}</p>
      ) : (
        <div className="console-table-wrap">
          <table className="console-table">
            <thead>
              <tr><th>{t("colWhen")}</th><th>{t("colWho")}</th><th>{t("revealPurpose")}</th><th>{t("colRecord")}</th><th>IP</th></tr>
            </thead>
            <tbody>
              {rows?.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.created_at).toLocaleString(t.locale, { dateStyle: "medium", timeStyle: "short" })}</td>
                  <td>{r.user_email}<span className="sub">{t.dynamic(`role_${r.user_role}`, "role_observer")}</span></td>
                  <td>{t.dynamic(`purpose_${r.purpose}`, "purpose_other")}{r.note && <span className="sub">{r.note}</span>}</td>
                  <td><code>{r.observation_id.slice(0, 8)}</code><span className="sub">{r.geoprivacy ?? ""}</span></td>
                  <td>{r.ip ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

