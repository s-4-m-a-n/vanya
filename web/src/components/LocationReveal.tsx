import { Lock, LockOpen } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "../api";
import { useT } from "../i18n";
import { ACCESS_PURPOSES, type AccessPurpose, type ExactLocation, type Observation } from "../types";

/** Generalised location for protected records, with an audited "show exact" action. */
export function LocationReveal({ obs, defaultPurpose = "verification" }: {
  obs: Observation;
  defaultPurpose?: AccessPurpose;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [purpose, setPurpose] = useState<AccessPurpose>(defaultPurpose);
  const [exact, setExact] = useState<ExactLocation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const km = obs.accuracy_m ? Math.round(obs.accuracy_m / 1000) : 0;
  const where = obs.latitude != null && obs.longitude != null
    ? `${obs.latitude.toFixed(obs.location_precision === "exact" ? 4 : 1)}, ${obs.longitude.toFixed(obs.location_precision === "exact" ? 4 : 1)}`
    : "—";

  if (obs.location_precision === "exact") {
    return (
      <span>
        {where}
        {obs.accuracy_m != null && ` (±${Math.round(obs.accuracy_m)} m)`}
      </span>
    );
  }

  async function reveal() {
    setError(null);
    try {
      setExact(await api.revealLocation(obs.id, purpose));
      setOpen(false);
    } catch (err) {
      setError(err instanceof ApiError && err.code ? t.dynamic(`err_${err.code}`, "err_generic") : t("err_generic"));
    }
  }

  return (
    <span className="stack tight">
      <span>
        <span className="chip protected"><Lock size={12} aria-hidden /> {t("protectedSpecies")}</span> {t("approxLocation", { km })} · {where}
      </span>
      {exact && (
        <span className="small">
          {t("exactLocation")}: {exact.latitude.toFixed(5)}, {exact.longitude.toFixed(5)}
          {exact.accuracy_m != null && ` (±${Math.round(exact.accuracy_m)} m)`}
          {exact.exif_latitude != null && exact.exif_longitude != null &&
            ` · ${t("photoGps")}: ${exact.exif_latitude.toFixed(5)}, ${exact.exif_longitude.toFixed(5)}`}
        </span>
      )}
      {!exact && !open && (
        <button className="link small" onClick={() => setOpen(true)}><LockOpen size={14} aria-hidden /> {t("revealExact")}</button>
      )}
      {open && (
        <span className="row center">
          <select value={purpose} onChange={(e) => setPurpose(e.target.value as AccessPurpose)} aria-label={t("revealPurpose")}>
            {ACCESS_PURPOSES.map((p) => (
              <option key={p} value={p}>{t(`purpose_${p}`)}</option>
            ))}
          </select>
          <button onClick={reveal}>{t("revealExact")}</button>
        </span>
      )}
      {open && <span className="muted small">{t("revealLogged")}</span>}
      {error && <span className="error small">{error}</span>}
    </span>
  );
}
