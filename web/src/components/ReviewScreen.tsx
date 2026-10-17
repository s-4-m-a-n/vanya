import { Ban, Check, ChevronLeft, LocateFixed, Search, SkipForward } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { getPosition } from "../device";
import { formatWhen } from "../format";
import { useT } from "../i18n";
import { useOnline } from "../syncManager";
import { REJECT_REASONS, type QueueItem, type RejectReason, type Taxon, type TaxonGroup, type User } from "../types";
import { LocationReveal } from "./LocationReveal";
import { Photo } from "./Photo";
import { SpeciesSearch } from "./SpeciesSearch";
import { TaxonLabel } from "./TaxonLabel";

const GROUPS: TaxonGroup[] = ["bird", "animal", "plant"];
const DEFAULT_RADIUS_KM = 100;

type Mode = "idle" | "search" | "reject";

export function ReviewScreen({ user, onUserChange }: { user: User; onUserChange: (u: User) => void }) {
  const t = useT();
  const online = useOnline();
  const [group, setGroup] = useState<TaxonGroup | "">("");
  const [anywhere, setAnywhere] = useState(false);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>("idle");
  const [reason, setReason] = useState<RejectReason>("unidentifiable");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());

  const hasArea = user.review_radius_km != null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.reviewQueue({ group: group || undefined, anywhere, limit: 20 });
      setItems(page.items);
      setTotal(page.total);
    } catch {
      setError(t("err_generic"));
    } finally {
      setLoading(false);
    }
    // `t` is a new function each render; the queue only depends on the filters.
  }, [group, anywhere]);

  useEffect(() => {
    if (online) void load();
  }, [load, online]);

  const current = items.find((item) => !skipped.has(item.id));

  function next() {
    setMode("idle");
    setComment("");
    setError(null);
  }

  async function submit(body: Parameters<typeof api.submitReview>[1]) {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await api.submitReview(current.id, { ...body, comment: comment.trim() || undefined });
      setItems((list) => list.filter((i) => i.id !== current.id));
      setTotal((n) => Math.max(0, n - 1));
      next();
      if (items.length <= 1) void load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("err_generic"));
    } finally {
      setBusy(false);
    }
  }

  async function setAreaHere() {
    const fix = await getPosition();
    if (!fix) {
      setError(t("locationUnavailable"));
      return;
    }
    const updated = await api.updateMe({
      review_area: { latitude: fix.latitude, longitude: fix.longitude, radius_km: DEFAULT_RADIUS_KM },
    });
    onUserChange(updated);
    setAnywhere(false);
    void load();
  }

  if (!online) return <main className="screen"><p className="empty">{t("reviewOffline")}</p></main>;

  return (
    <main className="screen stack">
      <div className="row filters">
        <select value={group} onChange={(e) => setGroup(e.target.value as TaxonGroup | "")}>
          <option value="">{t("groupAll")}</option>
          {GROUPS.map((g) => (
            <option key={g} value={g}>{t(`group_${g}`)}</option>
          ))}
        </select>
        <select
          value={anywhere || !hasArea ? "anywhere" : "area"}
          onChange={(e) => setAnywhere(e.target.value === "anywhere")}
        >
          {hasArea && <option value="area">{t("areaWithin", { km: user.review_radius_km ?? 0 })}</option>}
          <option value="anywhere">{t("areaAnywhere")}</option>
        </select>
        <button className="link small" onClick={setAreaHere}>
          <LocateFixed size={16} aria-hidden /> {t("setAreaHere")}
        </button>
      </div>

      <p className="muted small">{t("waitingCount", { n: total })}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {loading && !current && <p className="muted">…</p>}
      {!loading && !current && <p className="empty">{t("reviewEmpty")}</p>}

      {current && (
        <article className="card stack review-card">
          <Photo src={current.photo_url} clientUuid={current.client_uuid} className="preview" />
          <div className="observer-line">
            <span className="initial" aria-hidden>{current.observer.display_name[0]?.toUpperCase()}</span>
            <div className="grow">
              <strong>{current.observer.display_name}</strong>
              <p className="muted small">
                {t("reputationOf", { r: current.observer.reputation })} · {formatWhen(t, current.observed_at)}
                {current.habitat && ` · ${t(`habitat_${current.habitat}`)}`}
              </p>
            </div>
            {current.verification.is_rare && <span className="chip rare">★ {t("rareFind")}</span>}
          </div>
          <div className="small">
            <LocationReveal key={current.id} obs={current} />
          </div>
          {current.notes && <blockquote>{current.notes}</blockquote>}

          <section className="stack tight">
            <span className="subtle-label">{t("observerPick")}</span>
            <TaxonLabel taxon={current.taxon} />
          </section>

          {current.identification.candidates.length > 0 && (
            <section className="stack tight">
              <span className="subtle-label">{t("aiSuggestions")}</span>
              <ul className="suggestions">
                {current.identification.candidates.map((c) => (
                  <li key={c.rank}>
                    <span className="row center nowrap">
                      <span className="grow">{c.taxon?.common_name ?? c.common_name ?? c.scientific_name} <em className="muted small">{c.scientific_name}</em></span>
                      <span className="small">{Math.round(c.confidence * 100)}%</span>
                    </span>
                    <span className="confidence">
                      <span className="track">
                        <span className="bar" style={{ width: `${Math.round(c.confidence * 100)}%` }} />
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="stack tight">
            <span className="subtle-label">{t("whyReview")}</span>
            <div className="row">
              {current.verification.reasons.map((r) => (
                <span key={r} className="chip reason">{t.dynamic(`reason_${r}`, "reason_unknown")}</span>
              ))}
            </div>
            {current.verification.integrity_flags.some((f) => f.severity === "soft") && (
              <div className="row">
                {current.verification.integrity_flags
                  .filter((f) => f.severity === "soft")
                  .map((f) => (
                    <span key={f.code} className="chip soft">
                      {t.dynamic(`reason_${f.code}`, "reason_unknown")}
                      {f.detail && ` (${f.detail})`}
                    </span>
                  ))}
              </div>
            )}
          </section>

          <label>
            {t("noteOptional")}
            <textarea rows={2} maxLength={1000} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>

          {mode === "search" && (
            <SpeciesSearch onPick={(taxon: Taxon) => submit({ decision: "confirm", taxon_id: taxon.id })} />
          )}
          {mode === "reject" && (
            <div className="stack">
              <label>
                {t("rejectReason")}
                <select value={reason} onChange={(e) => setReason(e.target.value as RejectReason)}>
                  {REJECT_REASONS.map((r) => (
                    <option key={r} value={r}>{t(`reject_${r}`)}</option>
                  ))}
                </select>
              </label>
              <button className="danger solid" disabled={busy} onClick={() => submit({ decision: "reject", reason })}>
                <Ban size={18} aria-hidden /> {t("submitReject")}
              </button>
            </div>
          )}

          {mode === "idle" && (
            <div className="stack">
              {current.taxon && (
                <button className="primary" disabled={busy} onClick={() => submit({ decision: "confirm" })}>
                  <Check size={18} aria-hidden /> {t("confirmAs", { name: current.taxon.common_name ?? current.taxon.scientific_name })}
                </button>
              )}
              <div className="review-alt">
                <button onClick={() => setMode("search")}>
                  <Search size={18} aria-hidden /> {t("differentSpecies")}
                </button>
                <button className="danger" onClick={() => setMode("reject")}>
                  <Ban size={18} aria-hidden /> {t("reject")}
                </button>
              </div>
              <button className="link" style={{ alignSelf: "center" }} onClick={() => setSkipped((s) => new Set(s).add(current.id))}>
                {t("skip")} <SkipForward size={16} aria-hidden />
              </button>
            </div>
          )}
          {mode !== "idle" && (
            <button className="link" onClick={next}>
              <ChevronLeft size={16} aria-hidden /> {t("back")}
            </button>
          )}
        </article>
      )}
    </main>
  );
}
