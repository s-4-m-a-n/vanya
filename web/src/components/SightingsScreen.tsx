import { Camera, ChevronRight, Leaf, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { getDb, replaceObservations } from "../db";
import { formatWhen } from "../format";
import { useT, type MessageKey } from "../i18n";
import { awaitingServer } from "../pending";
import { retryCapture } from "../sync";
import { syncManager, useDataVersion, useOnline } from "../syncManager";
import type { Observation, QueuedCapture } from "../types";
import { Photo } from "./Photo";
import { TierChip } from "./RewardsSummary";
import { StatusChip } from "./StatusChip";
import { TaxonLabel } from "./TaxonLabel";

export type SightingFilter = "all" | "needs_id" | "in_review" | "verified";

const FILTERS: [SightingFilter, MessageKey][] = [
  ["all", "filterAll"],
  ["needs_id", "filterNeedsId"],
  ["in_review", "filterInReview"],
  ["verified", "filterVerified"],
];

function matches(filter: SightingFilter, obs: Observation): boolean {
  if (filter === "all") return true;
  if (filter === "in_review") return obs.status === "in_review" || obs.status === "submitted";
  return obs.status === filter;
}

export function SightingsScreen({ initialFilter = "all", onOpen, onCapture }: {
  initialFilter?: SightingFilter;
  onOpen: (id: string) => void;
  onCapture: () => void;
}) {
  const t = useT();
  const [filter, setFilter] = useState<SightingFilter>(initialFilter);
  const online = useOnline();
  const version = useDataVersion();
  const [loaded, setLoaded] = useState(false);
  const [queue, setQueue] = useState<QueuedCapture[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);

  // Refresh the cache from the server whenever we're online, and keep refreshing while
  // any sighting is still being checked.
  const checking = observations.some(awaitingServer);
  useEffect(() => {
    if (!online) return;
    const refresh = () =>
      api
        .myObservations()
        .then(async (page) => {
          await replaceObservations(await getDb(), page.items);
          syncManager.notify();
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 401) syncManager.onAuthExpired();
        });
    void refresh();
    if (!checking) return;
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [online, checking]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const db = await getDb();
      const [queued, cached] = await Promise.all([db.getAll("queue"), db.getAll("observations")]);
      if (cancelled) return;
      setQueue(queued.sort((a, b) => b.createdAt.localeCompare(a.createdAt)));
      setObservations(cached.sort((a, b) => b.observed_at.localeCompare(a.observed_at)));
      setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [version]);

  async function retry(item: QueuedCapture) {
    await retryCapture(await getDb(), item.clientUuid);
    syncManager.notify();
    void syncManager.run();
  }

  async function discard(item: QueuedCapture) {
    if (!confirm(t("confirmDelete"))) return;
    const db = await getDb();
    await Promise.all([db.delete("queue", item.clientUuid), db.delete("thumbnails", item.clientUuid)]);
    syncManager.notify();
  }

  if (!loaded) return <main className="screen" />;
  if (queue.length === 0 && observations.length === 0) {
    return (
      <main className="screen">
        <div className="empty-state">
          <span className="icon-circle large"><Leaf size={34} aria-hidden /></span>
          <p>{t("noSightings")}</p>
          <button className="primary" onClick={onCapture}>
            <Camera size={18} aria-hidden /> {t("startCapturing")}
          </button>
        </div>
      </main>
    );
  }

  const shown = observations.filter((o) => matches(filter, o));
  const count = (f: SightingFilter) =>
    observations.filter((o) => matches(f, o)).length + (f === "all" ? queue.length : 0);

  return (
    <main className="screen stack">
      <h1 className="section-title">
        {t("sightingsTitle")} <span className="count">{queue.length + observations.length}</span>
      </h1>
      <div className="filter-row" role="tablist">
        {FILTERS.map(([f, key]) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            className={filter === f ? "active" : ""}
            onClick={() => setFilter(f)}
          >
            {t(key)} <span className="n">{count(f)}</span>
          </button>
        ))}
      </div>
      <ul className="sightings">
        {filter === "all" &&
          queue.map((item) => (
            <li key={item.clientUuid} className="sighting">
              <Photo clientUuid={item.clientUuid} className="thumb" />
              <div className="grow">
                <span className={`chip ${item.state}`}>
                  {item.state === "failed" ? t("failed") : item.state === "uploading" ? t("uploading") : t("queued")}
                </span>
                {item.state === "failed" && item.lastError && (
                  <p className="error small">{t.dynamic(`err_${item.lastError}`, "err_generic")}</p>
                )}
                <p className="muted small">{formatWhen(t, item.createdAt)}</p>
                {item.state === "failed" && (
                  <div className="row">
                    <button onClick={() => retry(item)}>
                      <RotateCw size={16} aria-hidden /> {t("retry")}
                    </button>
                    <button className="danger" onClick={() => discard(item)}>
                      <Trash2 size={16} aria-hidden /> {t("delete")}
                    </button>
                  </div>
                )}
              </div>
            </li>
          ))}
        {shown.map((obs) => {
          const needsId = obs.status === "needs_id";
          return (
            <li key={obs.id}>
              <button className={`sighting ${needsId ? "attention" : ""}`} onClick={() => onOpen(obs.id)}>
                <Photo src={obs.photo_url} clientUuid={obs.client_uuid} className="thumb" />
                <div className="grow">
                  {obs.taxon && !needsId ? (
                    <TaxonLabel taxon={obs.taxon} />
                  ) : (
                    <strong>{obs.identification.candidates[0]?.common_name ?? t("unidentified")}?</strong>
                  )}
                  <div className="row center">
                    <StatusChip status={obs.status} />
                    {obs.rewards?.rarity_tier && obs.rewards.rarity_tier !== "common" && (
                      <TierChip tier={obs.rewards.rarity_tier} />
                    )}
                    {obs.rewards?.status && (
                      <span className={`xp small ${obs.rewards.status}`}>+{obs.rewards.xp} XP</span>
                    )}
                  </div>
                  {needsId ? (
                    <span className="cta-text">{t("tapToConfirm")}</span>
                  ) : (
                    <p className="muted small">{formatWhen(t, obs.observed_at)}</p>
                  )}
                </div>
                <ChevronRight size={18} className="chevron" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
      {shown.length === 0 && (filter !== "all" || queue.length === 0) && (
        <p className="empty">{t("noSightingsFilter")}</p>
      )}
    </main>
  );
}
