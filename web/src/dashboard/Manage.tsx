import { PenLine } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../api";
import { SpeciesSearch } from "../components/SpeciesSearch";
import { useT } from "../i18n";
import type { Taxon } from "../types";
import { dashboardApi } from "./api";
import { AlertSettingsTab } from "./Insights";
import { taxonName } from "./Panels";
import type { Member, Org, Region, WatchEntry } from "./types";

type Tab = "regions" | "watchlist" | "members" | "alerts";

interface Props {
  org: Org;
  regions: Region[];
  onRegionsChanged: () => void;
  onWatchlistChanged: () => void;
  onStartDrawing: () => void;
}

export function Manage({ org, regions, onRegionsChanged, onWatchlistChanged, onStartDrawing }: Props) {
  const t = useT();
  const [tab, setTab] = useState<Tab>("regions");
  const [error, setError] = useState<string | null>(null);
  const isAdmin = org.my_role === "admin";

  async function run(action: () => Promise<unknown>, after: () => void) {
    setError(null);
    try {
      await action();
      after();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("err_generic"));
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    const name = file.name.replace(/\.(geo)?json$/i, "");
    await run(async () => dashboardApi.addRegion(org.id, name, JSON.parse(await file.text())), onRegionsChanged);
  }

  return (
    <section className="dash-card">
      <h2>{t("dashManage")}</h2>
      <div className="dash-tabs">
        {(["regions", "watchlist", "members", "alerts"] as Tab[]).map((name) => (
          <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
            {t(
              name === "regions"
                ? "dashRegions"
                : name === "watchlist"
                  ? "dashWatchlist"
                  : name === "members"
                    ? "dashMembers"
                    : "dashAlertSettings",
            )}
          </button>
        ))}
      </div>
      {error && <p className="error small">{error}</p>}

      {tab === "regions" && (
        <div className="stack">
          <table className="dash-table">
            <tbody>
              {regions.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td className="num">{t("dashArea", { km2: r.area_km2.toLocaleString(t.locale) })}</td>
                  <td className="num">
                    {isAdmin && (
                      <button className="link small" onClick={() => run(() => dashboardApi.removeRegion(org.id, r.id), onRegionsChanged)}>
                        {t("dashRemove")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {isAdmin ? (
            <div className="row">
              <button onClick={onStartDrawing}><PenLine size={16} aria-hidden /> {t("dashDrawRegion")}</button>
              <label className="big-button" style={{ minHeight: 44, padding: "0 1rem", fontSize: "0.95rem" }}>
                {t("dashUploadGeojson")}
                <input type="file" accept=".geojson,.json,application/geo+json,application/json" hidden onChange={(e) => upload(e.target.files?.[0])} />
              </label>
            </div>
          ) : (
            <p className="dash-note">{t("dashAdminOnly")}</p>
          )}
        </div>
      )}

      {tab === "watchlist" && <Watchlist org={org} onChanged={onWatchlistChanged} run={run} />}
      {tab === "members" && <Members org={org} run={run} />}
      {tab === "alerts" && <AlertSettingsTab org={org} />}
    </section>
  );
}

type Run = (action: () => Promise<unknown>, after: () => void) => Promise<void>;

function Watchlist({ org, onChanged, run }: { org: Org; onChanged: () => void; run: Run }) {
  const t = useT();
  const [entries, setEntries] = useState<WatchEntry[]>([]);
  const [picked, setPicked] = useState<Taxon | null>(null);
  const load = () => dashboardApi.watchlist(org.id).then(setEntries).catch(() => {});
  useEffect(() => {
    void load();
  }, [org.id]); // reload only when the organisation changes

  const after = () => {
    setPicked(null);
    void load();
    onChanged();
  };

  return (
    <div className="stack">
      <table className="dash-table">
        <tbody>
          {entries.map((e) => (
            <tr key={e.taxon.id}>
              <td>{taxonName(e.taxon)} <em className="dash-note">{e.taxon.scientific_name}</em></td>
              <td><span className="chip">{t(`dashCat_${e.category}`)}</span></td>
              <td className="num">
                <button className="link small" onClick={() => run(() => dashboardApi.unwatch(org.id, e.taxon.id), after)}>
                  {t("dashRemove")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {picked ? (
        <div className="row center">
          <strong className="grow">{picked.common_name ?? picked.scientific_name}</strong>
          <button onClick={() => run(() => dashboardApi.watch(org.id, picked.id, "invasive"), after)}>{t("dashAddInvasive")}</button>
          <button onClick={() => run(() => dashboardApi.watch(org.id, picked.id, "priority"), after)}>{t("dashAddPriority")}</button>
          <button className="link" onClick={() => setPicked(null)}>{t("cancel")}</button>
        </div>
      ) : (
        <SpeciesSearch onPick={setPicked} />
      )}
    </div>
  );
}

function Members({ org, run }: { org: Org; run: Run }) {
  const t = useT();
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("analyst");
  const load = () => dashboardApi.members(org.id).then(setMembers).catch(() => {});
  useEffect(() => {
    void load();
  }, [org.id]); // reload only when the organisation changes
  const isAdmin = org.my_role === "admin";

  function add(event: FormEvent) {
    event.preventDefault();
    void run(() => dashboardApi.addMember(org.id, email, role), () => {
      setEmail("");
      void load();
    });
  }

  return (
    <div className="stack">
      <table className="dash-table">
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name} <span className="dash-note">{m.email}</span></td>
              <td>{t(`dashRole_${m.role}`)}</td>
              <td className="num">
                {isAdmin && (
                  <button className="link small" onClick={() => run(() => dashboardApi.removeMember(org.id, m.user_id), () => void load())}>
                    {t("dashRemove")}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {isAdmin ? (
        <form className="row" onSubmit={add}>
          <input className="grow" type="email" required placeholder={t("email")} value={email} onChange={(e) => setEmail(e.target.value)} style={{ marginTop: 0 }} />
          <select value={role} onChange={(e) => setRole(e.target.value as Member["role"])} style={{ width: "auto", marginTop: 0 }}>
            <option value="analyst">{t("dashRole_analyst")}</option>
            <option value="admin">{t("dashRole_admin")}</option>
          </select>
          <button>{t("dashAddMember")}</button>
        </form>
      ) : (
        <p className="dash-note">{t("dashAdminOnly")}</p>
      )}
    </div>
  );
}
