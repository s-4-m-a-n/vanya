import { Download, Loader2, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { SpeciesSearch } from "../components/SpeciesSearch";
import { useT, type MessageKey } from "../i18n";
import { HABITATS, type Habitat, type RarityTier, type TaxonGroup } from "../types";
import { GROUPS } from "./api";
import { taxonName } from "./Panels";
import { EMPTY_FILTERS, type Filters, type Region } from "./types";

const PERIODS: [number | null, MessageKey][] = [
  [30, "dashLast30"],
  [90, "dashLast90"],
  [365, "dashLast365"],
  [null, "dashAllTime"],
];
const TIERS: RarityTier[] = ["common", "uncommon", "rare", "very_rare", "exceptional"];
// Conservation status choices, each covering one or more IUCN codes.
const STATUS_GROUPS: [MessageKey, string[]][] = [
  ["statusThreatened", ["CR", "EN", "VU"]],
  ["iucn_NT", ["NT"]],
  ["iucn_LC", ["LC"]],
  ["statusUnknown", ["DD", "NE"]],
];

/** How many filters beyond period, region and group are switched on. */
export function advancedCount(f: Filters): number {
  return [
    f.taxon, f.from || f.to, f.habitats.length, f.iucn.length, f.rarity.length,
    f.verification, f.watchlist, f.protected != null,
  ].filter(Boolean).length;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function Choice({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className={`filter-chip ${on ? "on" : ""}`} aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  );
}

function Segmented<V extends string>({ value, options, onChange }: {
  value: V | null;
  options: [V | null, string][];
  onChange: (v: V | null) => void;
}) {
  return (
    <div className="segmented">
      {options.map(([v, label]) => (
        <button key={label} type="button" className={value === v ? "active" : ""} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** Period, region and group selects, plus a "Filters" panel for everything else and CSV export. */
export function FilterBar({ filters, onChange, regions, onExport }: {
  filters: Filters;
  onChange: (f: Filters) => void;
  regions: Region[];
  onExport: () => Promise<void>;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [pickingSpecies, setPickingSpecies] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const custom = !!(filters.from || filters.to);
  const count = advancedCount(filters);
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !panel.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  async function exportCsv() {
    setExporting(true);
    try {
      await onExport();
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="console-filters" ref={panel}>
      <select
        value={custom ? "custom" : filters.days ?? ""}
        aria-label={t("filterDates")}
        onChange={(e) => {
          if (e.target.value === "custom") {
            const today = new Date().toISOString().slice(0, 10);
            const start = new Date(Date.now() - (filters.days ?? 90) * 86_400_000).toISOString().slice(0, 10);
            set({ from: start, to: today });
            setOpen(true);
          } else {
            set({ days: e.target.value ? Number(e.target.value) : null, from: null, to: null });
          }
        }}
      >
        {PERIODS.map(([days, key]) => <option key={key} value={days ?? ""}>{t(key)}</option>)}
        <option value="custom">{t("dashCustomRange")}</option>
      </select>
      <select value={filters.regionId ?? ""} onChange={(e) => set({ regionId: e.target.value ? Number(e.target.value) : null })} aria-label={t("dashRegions")}>
        <option value="">{t("dashAllRegions")}</option>
        {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
      </select>
      <select value={filters.group ?? ""} onChange={(e) => set({ group: (e.target.value || null) as TaxonGroup | null })} aria-label={t("dashAllGroups")}>
        <option value="">{t("dashAllGroups")}</option>
        {GROUPS.map((g) => <option key={g} value={g}>{t(`group_${g}_one`)}</option>)}
      </select>
      <button className={`filters-button ${count ? "has" : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <SlidersHorizontal size={16} aria-hidden /> {t("filtersButton")}
        {count > 0 && <span className="console-badge">{count}</span>}
      </button>
      <button onClick={exportCsv} disabled={exporting} title={t("exportCsv")}>
        {exporting ? <Loader2 size={16} className="spin" aria-hidden /> : <Download size={16} aria-hidden />}
        {exporting ? t("exporting") : t("exportCsv")}
      </button>

      {open && (
        <div className="filter-panel" role="dialog" aria-label={t("filtersTitle")}>
          <div className="filter-panel-head">
            <h2>{t("filtersTitle")}</h2>
            <button className="icon-ghost" onClick={() => setOpen(false)} aria-label={t("reportClose")}><X size={18} /></button>
          </div>
          <div className="filter-panel-body">
            <section>
              <h3>{t("dashSpecies")}</h3>
              {filters.taxon && !pickingSpecies ? (
                <div className="row center">
                  <span className="chip">{taxonName(filters.taxon)} <em>{filters.taxon.scientific_name}</em></span>
                  <button className="link small" onClick={() => setPickingSpecies(true)}>{t("filterChange")}</button>
                  <button className="link small" onClick={() => set({ taxon: null })}>{t("dashClearSpecies")}</button>
                </div>
              ) : (
                <SpeciesSearch
                  onPick={(taxon) => {
                    set({ taxon: {
                      id: taxon.id, scientific_name: taxon.scientific_name, common_name: taxon.common_name,
                      taxon_group: taxon.taxon_group, iucn_status: taxon.iucn_status,
                    } });
                    setPickingSpecies(false);
                  }}
                />
              )}
            </section>
            <section>
              <h3>{t("filterDates")}</h3>
              <div className="row">
                <label className="grow">{t("filterFrom")}
                  <input type="date" value={filters.from ?? ""} max={filters.to ?? undefined} onChange={(e) => set({ from: e.target.value || null })} />
                </label>
                <label className="grow">{t("filterTo")}
                  <input type="date" value={filters.to ?? ""} min={filters.from ?? undefined} onChange={(e) => set({ to: e.target.value || null })} />
                </label>
              </div>
            </section>
            <section>
              <h3>{t("habitat")}</h3>
              <div className="chip-row">
                {HABITATS.map((h: Habitat) => (
                  <Choice key={h} on={filters.habitats.includes(h)} onClick={() => set({ habitats: toggle(filters.habitats, h) })}>
                    {t(`habitat_${h}`)}
                  </Choice>
                ))}
              </div>
            </section>
            <section>
              <h3>{t("filterStatus")}</h3>
              <div className="chip-row">
                {STATUS_GROUPS.map(([key, codes]) => {
                  const on = codes.every((c) => filters.iucn.includes(c));
                  return (
                    <Choice key={key} on={on} onClick={() => set({
                      iucn: on ? filters.iucn.filter((c) => !codes.includes(c)) : [...new Set([...filters.iucn, ...codes])],
                    })}>
                      {t(key)}
                    </Choice>
                  );
                })}
              </div>
            </section>
            <section>
              <h3>{t("filterRarity")}</h3>
              <div className="chip-row">
                {TIERS.map((tier) => (
                  <Choice key={tier} on={filters.rarity.includes(tier)} onClick={() => set({ rarity: toggle(filters.rarity, tier) })}>
                    {t(`tier_${tier}`)}
                  </Choice>
                ))}
              </div>
            </section>
            <section className="filter-grid">
              <div>
                <h3>{t("filterVerification")}</h3>
                <Segmented value={filters.verification} onChange={(v) => set({ verification: v })}
                  options={[[null, t("filterAny")], ["auto", t("verifAuto")], ["expert", t("verifExpert")]]} />
              </div>
              <div>
                <h3>{t("filterWatchlist")}</h3>
                <Segmented value={filters.watchlist} onChange={(v) => set({ watchlist: v })}
                  options={[[null, t("filterAny")], ["invasive", t("dashCat_invasive")], ["priority", t("dashCat_priority")]]} />
              </div>
              <div>
                <h3>{t("filterLocation")}</h3>
                <Segmented
                  value={filters.protected == null ? null : filters.protected ? "yes" : "no"}
                  onChange={(v) => set({ protected: v == null ? null : v === "yes" })}
                  options={[[null, t("filterAny")], ["yes", t("locProtected")], ["no", t("locOpen")]]} />
              </div>
            </section>
          </div>
          <div className="filter-panel-foot">
            <button className="link" onClick={() => onChange({ ...EMPTY_FILTERS, regionId: filters.regionId })}>{t("filterClear")}</button>
            <button className="primary" onClick={() => setOpen(false)}>{t("filterDone")}</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Removable chips for the active advanced filters, shown above the results. */
export function ActiveFilters({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  const t = useT();
  if (advancedCount(filters) === 0) return null;
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const fmt = (d: string) => new Date(d + "T00:00").toLocaleDateString(t.locale, { dateStyle: "medium" });
  const chips: [string, () => void][] = [];
  if (filters.taxon) chips.push([taxonName(filters.taxon), () => set({ taxon: null })]);
  if (filters.from || filters.to)
    chips.push([`${filters.from ? fmt(filters.from) : "…"} – ${filters.to ? fmt(filters.to) : "…"}`, () => set({ from: null, to: null })]);
  filters.habitats.forEach((h) => chips.push([t(`habitat_${h}`), () => set({ habitats: filters.habitats.filter((x) => x !== h) })]));
  STATUS_GROUPS.forEach(([key, codes]) => {
    if (codes.every((c) => filters.iucn.includes(c)))
      chips.push([t(key), () => set({ iucn: filters.iucn.filter((c) => !codes.includes(c)) })]);
  });
  filters.rarity.forEach((r) => chips.push([t(`tier_${r}`), () => set({ rarity: filters.rarity.filter((x) => x !== r) })]));
  if (filters.verification)
    chips.push([`${t("filterVerification")}: ${t(filters.verification === "auto" ? "verifAuto" : "verifExpert")}`, () => set({ verification: null })]);
  if (filters.watchlist) chips.push([t(`dashCat_${filters.watchlist}`), () => set({ watchlist: null })]);
  if (filters.protected != null)
    chips.push([`${t("filterLocation")}: ${t(filters.protected ? "locProtected" : "locOpen")}`, () => set({ protected: null })]);

  return (
    <div className="active-filters">
      <span className="dash-note">{t("filterActive")}</span>
      {chips.map(([label, remove]) => (
        <span key={label} className="chip">
          {label}
          <button className="link small" onClick={remove} aria-label={`${t("dashRemove")} ${label}`}><X size={13} aria-hidden /></button>
        </span>
      ))}
      <button className="link small" onClick={() => onChange({ ...EMPTY_FILTERS, regionId: filters.regionId, group: filters.group, days: filters.days })}>
        {t("filterClear")}
      </button>
    </div>
  );
}
