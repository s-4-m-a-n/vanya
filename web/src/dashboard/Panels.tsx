import { useT } from "../i18n";
import type { Changes, NewRecord, RecordItem, Summary, TaxonRef } from "./types";

export function taxonName(taxon: TaxonRef): string {
  return taxon.common_name ?? taxon.scientific_name;
}

function compact(n: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: n >= 10_000 ? "compact" : "standard" }).format(n);
}

export function KpiTiles({ summary }: { summary: Summary }) {
  const t = useT();
  const tiles: [string, number][] = [
    [t("dashObservations"), summary.observations],
    [t("dashSpecies"), summary.species],
    [t("dashObservers"), summary.observers],
    [t("dashRare"), summary.rare_finds],
    [t("dashThreatened"), summary.threatened],
    [t("dashInvasive"), summary.invasive],
  ];
  return (
    <div className="dash-tiles">
      {tiles.map(([label, value]) => (
        <div key={label} className="dash-tile">
          <div className="label">{label}</div>
          <div className="value">{compact(value, t.locale)}</div>
        </div>
      ))}
    </div>
  );
}

export function TopSpecies({ summary, onPick }: { summary: Summary; onPick: (t: TaxonRef) => void }) {
  const t = useT();
  if (!summary.top_species.length) return <p className="dash-note">{t("dashNone")}</p>;
  return (
    <table className="dash-table">
      <tbody>
        {summary.top_species.map(({ taxon, count }) => (
          <tr key={taxon.id}>
            <td>
              <button className="link small" onClick={() => onPick(taxon)}>{taxonName(taxon)}</button>{" "}
              <em className="dash-note">{taxon.scientific_name}</em>
            </td>
            <td className="num">{count.toLocaleString(t.locale)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RecordList({ items, empty }: { items: RecordItem[]; empty: string }) {
  const t = useT();
  if (!items.length) return <p className="dash-note">{empty}</p>;
  return (
    <ul className="dash-list">
      {items.map((r) => (
        <li key={r.id}>
          <img src={r.photo_url} alt="" loading="lazy" />
          <div className="grow">
            <div>
              {r.geoprivacy !== "open" && <span aria-label={t("protectedSpecies")}>🔒 </span>}
              {taxonName(r.taxon)}
            </div>
            <div className="meta">
              {new Date(r.observed_at).toLocaleDateString(t.locale)}
              {r.rarity_tier && ` · ${t(`tier_${r.rarity_tier}`)}`}
              {r.taxon.iucn_status !== "NE" && ` · ${t.dynamic(`iucn_${r.taxon.iucn_status}`, "iucn_NE")}`}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function NewRecords({ items }: { items: NewRecord[] }) {
  const t = useT();
  if (!items.length) return <p className="dash-note">{t("dashNone")}</p>;
  return (
    <table className="dash-table">
      <thead>
        <tr><th>{t("dashSpecies")}</th><th>{t("dashFirstSeen")}</th></tr>
      </thead>
      <tbody>
        {items.map((r) => (
          <tr key={r.taxon.id}>
            <td>{taxonName(r.taxon)} <span className="dash-note">{r.records === 1 ? t("dashRecordsOne") : t("dashRecords", { n: r.records })}</span></td>
            <td>{new Date(r.first_seen).toLocaleDateString(t.locale)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ChangesPanel({ changes }: { changes: Changes }) {
  const t = useT();
  const block = (title: string, items: Changes["increases"], arrow: string) => (
    <div>
      <h3 className="small">{title}</h3>
      {items.length === 0 ? (
        <p className="dash-note">{t("dashNone")}</p>
      ) : (
        <table className="dash-table">
          <thead>
            <tr><th>{t("dashSpecies")}</th><th className="num">{t("dashPrevious")}</th><th className="num">{t("dashCurrent")}</th></tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.taxon.id}>
                <td>{arrow} {taxonName(c.taxon)}</td>
                <td className="num">{c.previous}</td>
                <td className="num">{c.current}{c.change_pct != null && ` (${c.change_pct > 0 ? "+" : ""}${c.change_pct}%)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );

  return (
    <div className="stack">
      <p className="dash-note">{t("dashChangesNote")}</p>
      {block(t("dashIncreases"), changes.increases, "▲")}
      {block(t("dashDecreases"), changes.decreases, "▼")}
    </div>
  );
}
