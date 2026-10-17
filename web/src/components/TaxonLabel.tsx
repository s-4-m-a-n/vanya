import { useT } from "../i18n";
import type { Taxon } from "../types";

export const THREATENED = new Set(["CR", "EN", "VU"]);

export function TaxonLabel({ taxon, fallbackName }: { taxon: Taxon | null; fallbackName?: string }) {
  const t = useT();
  if (!taxon) {
    return (
      <span className="taxon">
        <em>{fallbackName}</em>
      </span>
    );
  }
  return (
    <span className="taxon">
      <strong>{taxon.common_name ?? taxon.scientific_name}</strong>
      {taxon.common_name && <em>{taxon.scientific_name}</em>}
      {taxon.iucn_status !== "NE" && (
        <span className={`iucn ${THREATENED.has(taxon.iucn_status) ? "threatened" : ""}`}>
          {t.dynamic(`iucn_${taxon.iucn_status}`, "iucn_NE")}
        </span>
      )}
    </span>
  );
}
