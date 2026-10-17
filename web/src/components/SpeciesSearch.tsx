import { useEffect, useState } from "react";

import { api } from "../api";
import { useT } from "../i18n";
import { useOnline } from "../syncManager";
import type { Taxon } from "../types";
import { TaxonLabel } from "./TaxonLabel";

export function SpeciesSearch({ onPick }: { onPick: (taxon: Taxon) => void }) {
  const t = useT();
  const online = useOnline();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Taxon[] | null>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2 || !online) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const page = await api.searchTaxa(q, t.locale);
        if (!cancelled) setResults(page.items);
      } catch {
        if (!cancelled) setResults([]);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, online, t.locale]);

  if (!online) return <p className="muted small">{t("searchOffline")}</p>;

  return (
    <div className="stack">
      <input
        type="search"
        placeholder={t("searchPlaceholder")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      {results?.length === 0 && <p className="muted small">{t("noResults")}</p>}
      <ul className="choices">
        {results?.map((taxon) => (
          <li key={taxon.id}>
            <button className="choice" onClick={() => onPick(taxon)}>
              <TaxonLabel taxon={taxon} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
