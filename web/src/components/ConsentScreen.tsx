import { Binoculars, HeartHandshake, MapPin, TreePine } from "lucide-react";
import { useState } from "react";

import { api } from "../api";
import { useT } from "../i18n";
import type { User } from "../types";

export function ConsentScreen({ onAccepted }: { onAccepted: (user: User) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function accept() {
    setBusy(true);
    setError(false);
    try {
      onAccepted(await api.updateMe({ location_consent: true }));
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="screen stack">
      <section className="card stack">
        <span className="icon-circle large">
          <MapPin size={34} aria-hidden />
        </span>
        <h1>{t("consentTitle")}</h1>
        <p>{t("consentBody")}</p>
      </section>
      <section className="card stack">
        <h2>{t("safetyTitle")}</h2>
        <ul className="safety-list">
          <li><Binoculars size={20} aria-hidden />{t("safety1")}</li>
          <li><HeartHandshake size={20} aria-hidden />{t("safety2")}</li>
          <li><TreePine size={20} aria-hidden />{t("safety3")}</li>
        </ul>
      </section>
      {error && <p className="error" role="alert">{t("err_generic")}</p>}
      <button className="primary" onClick={accept} disabled={busy}>
        {t("consentAccept")}
      </button>
    </main>
  );
}
