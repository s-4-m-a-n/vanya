import { CalendarDays, ChevronLeft, MapPin, NotebookPen, Search, Trash2, Trees } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api";
import { getDb } from "../db";
import { formatWhen } from "../format";
import { useT } from "../i18n";
import { awaitingServer } from "../pending";
import { syncManager, useDataVersion, useOnline } from "../syncManager";
import type { Observation, Taxon } from "../types";
import { Photo } from "./Photo";
import { RewardsSummary } from "./RewardsSummary";
import { SpeciesSearch } from "./SpeciesSearch";
import { StatusChip } from "./StatusChip";
import { TaxonLabel, THREATENED } from "./TaxonLabel";
import { VerificationPanel } from "./VerificationPanel";

export function ObservationDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const t = useT();
  const online = useOnline();
  const version = useDataVersion();
  const [obs, setObs] = useState<Observation | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    getDb()
      .then((db) => db.get("observations", id))
      .then((value) => setObs(value ?? null));
  }, [id, version]);

  // Verification and XP run in the background after submission; poll until both settle.
  const checking = obs ? awaitingServer(obs) : false;
  useEffect(() => {
    if (!checking || !online) return;
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts++;
      try {
        const fresh = await api.getObservation(id);
        if (!awaitingServer(fresh) || attempts >= 45) {
          clearInterval(timer);
          await (await getDb()).put("observations", fresh);
          syncManager.notify();
        }
      } catch {
        if (attempts >= 45) clearInterval(timer);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [checking, online, id]);

  async function choose(taxon: Taxon) {
    setBusy(true);
    setError(false);
    try {
      const updated = await api.chooseSpecies(id, taxon.id);
      await (await getDb()).put("observations", updated);
      setSearching(false);
      syncManager.notify();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(t("confirmDelete"))) return;
    try {
      await api.deleteObservation(id);
      const db = await getDb();
      await db.delete("observations", id);
      syncManager.notify();
      onBack();
    } catch {
      setError(true);
    }
  }

  if (!obs) return null;
  const ident = obs.identification;
  const candidates = ident.candidates.filter((c) => c.in_scope);
  const outOfScope = ident.candidates.length > 0 && candidates.length === 0;
  const named = obs.taxon && obs.status !== "needs_id" ? obs.taxon : null;

  return (
    <main className="screen stack">
      <div className="detail-top">
        <button className="back" onClick={onBack}>
          <ChevronLeft size={18} aria-hidden /> {t("back")}
        </button>
      </div>
      <Photo src={obs.photo_url} clientUuid={obs.client_uuid} className="preview" />

      <div className="title-block">
        <div className="grow stack tight">
          <h1>{named ? named.common_name ?? named.scientific_name : t("unidentified")}</h1>
          {named?.common_name && <em>{named.scientific_name}</em>}
          {named && named.iucn_status !== "NE" && (
            <span className={`iucn ${THREATENED.has(named.iucn_status) ? "threatened" : ""}`}>
              {t.dynamic(`iucn_${named.iucn_status}`, "iucn_NE")}
            </span>
          )}
        </div>
        <StatusChip status={obs.status} />
      </div>

      <VerificationPanel obs={obs} />
      {obs.rewards && <RewardsSummary rewards={obs.rewards} />}

      {obs.status === "needs_id" && (
        <section className="card stack">
          {ident.error && <p className="muted">{t("idUnavailable")}</p>}
          {ident.is_organism === false && <p className="muted">{t("notOrganism")}</p>}
          {outOfScope && <p className="muted">{t("outOfScope")}</p>}
          {candidates.length > 0 && !searching && (
            <>
              <h2>{t("candidatesTitle")}</h2>
              <ul className="choices">
                {candidates.map((c) => (
                  <li key={c.rank}>
                    <button
                      className="choice"
                      disabled={busy || !online || !c.taxon}
                      onClick={() => c.taxon && choose(c.taxon)}
                    >
                      <TaxonLabel taxon={c.taxon} fallbackName={c.scientific_name} />
                      <span className="confidence">
                        <span className="track">
                          <span className="bar" style={{ width: `${Math.round(c.confidence * 100)}%` }} />
                        </span>
                        <span className="small muted">{t("match", { p: Math.round(c.confidence * 100) })}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {ident.explanation && <p className="muted small">{ident.explanation}</p>}
            </>
          )}
          {searching || candidates.length === 0 ? (
            <SpeciesSearch onPick={choose} />
          ) : (
            <button className="link" onClick={() => setSearching(true)}>
              <Search size={16} aria-hidden /> {t("somethingElse")}
            </button>
          )}
        </section>
      )}

      <section className="card facts">
        <div className="fact">
          <CalendarDays size={20} aria-hidden />
          <div>
            <span className="label">{t("seen")}</span>
            <p>{formatWhen(t, obs.observed_at)}</p>
          </div>
        </div>
        <div className="fact">
          <MapPin size={20} aria-hidden />
          <div>
            <span className="label">{t("location")}</span>
            <p>
              {obs.latitude != null && obs.longitude != null && `${obs.latitude.toFixed(4)}, ${obs.longitude.toFixed(4)}`}
              {obs.accuracy_m != null && ` (±${Math.round(obs.accuracy_m)} m)`}
              {obs.location_source === "exif" && ` · ${t("fromPhoto")}`}
            </p>
            {obs.geoprivacy && (
              <p className="muted small">
                {obs.geoprivacy === "open"
                  ? t("publicOpen")
                  : obs.geoprivacy === "hidden"
                    ? t("publicHidden")
                    : t("publicObscured", { km: 20 })}
              </p>
            )}
          </div>
        </div>
        {obs.habitat && (
          <div className="fact">
            <Trees size={20} aria-hidden />
            <div>
              <span className="label">{t("habitat")}</span>
              <p>{t(`habitat_${obs.habitat}`)}</p>
            </div>
          </div>
        )}
        {obs.notes && (
          <div className="fact">
            <NotebookPen size={20} aria-hidden />
            <p>{obs.notes}</p>
          </div>
        )}
      </section>

      {error && <p className="error" role="alert">{t("err_generic")}</p>}
      {online && (
        <button className="danger ghost" onClick={remove}>
          <Trash2 size={16} aria-hidden /> {t("delete")}
        </button>
      )}
    </main>
  );
}
