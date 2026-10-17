import { BadgeCheck, Ban, Hourglass, Loader2 } from "lucide-react";

import { useT } from "../i18n";
import type { Observation } from "../types";

/** What happened to the sighting after the observer submitted it. */
export function VerificationPanel({ obs }: { obs: Observation }) {
  const t = useT();
  const v = obs.verification;
  const corrected = obs.observer_taxon && obs.taxon && obs.observer_taxon.id !== obs.taxon.id;
  const notes = v.reviews.filter((r) => r.comment);

  let summary: string | null = null;
  if (obs.status === "submitted") summary = t("checkingNote");
  else if (obs.status === "in_review") summary = t("inReviewNote");
  else if (obs.status === "verified")
    summary = v.method === "auto" ? t("verifiedAuto") : t("verifiedExpert");
  else if (obs.status === "rejected" && v.rejection_reason)
    summary = t("rejectedNote", {
      reason: t.dynamic(`reject_${v.rejection_reason}`, "reason_unknown"),
    });
  if (!summary) return null;

  const icon =
    obs.status === "verified" ? <BadgeCheck size={22} aria-hidden />
    : obs.status === "rejected" ? <Ban size={22} aria-hidden />
    : obs.status === "submitted" ? <Loader2 size={22} className="spin" aria-hidden />
    : <Hourglass size={22} aria-hidden />;

  return (
    <section className={`card verification ${obs.status}`}>
      <span className="icon-circle">{icon}</span>
      <div className="body">
        <p>{summary}</p>
        {(v.is_rare || (obs.status === "in_review" && v.required_reviews != null)) && (
          <div className="row center">
            {v.is_rare && <span className="chip rare">★ {t("rareFind")}</span>}
            {obs.status === "in_review" && v.required_reviews != null && (
              <span className="muted small">
                {t("reviewProgress", { n: v.review_count, m: v.required_reviews })}
              </span>
            )}
          </div>
        )}
        {corrected && obs.observer_taxon && (
          <p className="muted small">
            {t("correctedNote", {
              name: obs.observer_taxon.common_name ?? obs.observer_taxon.scientific_name,
            })}
          </p>
        )}
        {notes.length > 0 && (
          <div className="stack tight">
            <span className="muted small">{t("expertNotes")}</span>
            {notes.map((r, i) => (
              <blockquote key={i}>
                {r.comment}
                {r.reviewer_name && <cite> · {r.reviewer_name}</cite>}
              </blockquote>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
