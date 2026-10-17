import { Trophy } from "lucide-react";

import { useT } from "../i18n";
import type { Rewards } from "../types";

const NOTES = { repeat: "noteRepeat", restricted_area: "noteRestricted", daily_cap: "noteDailyCap" } as const;

export function TierChip({ tier }: { tier: Rewards["rarity_tier"] }) {
  const t = useT();
  if (!tier) return null;
  return <span className={`chip tier ${tier}`}>{t(`tier_${tier}`)}</span>;
}

/** XP for one sighting: provisional while in review, confirmed once verified. */
export function RewardsSummary({ rewards }: { rewards: Rewards }) {
  const t = useT();
  if (!rewards.status && !rewards.note) return null;
  return (
    <section className="card stack tight rewards">
      <div className="row center">
        <Trophy size={20} className="xp" aria-hidden />
        {rewards.status && (
          <strong className={`xp ${rewards.status}`}>
            {rewards.status === "provisional"
              ? t("xpPending", { n: rewards.xp })
              : t("xpEarned", { n: rewards.xp })}
          </strong>
        )}
        <TierChip tier={rewards.rarity_tier} />
      </div>
      {rewards.entries.length > 1 && (
        <ul className="plain small muted">
          {rewards.entries.map((e) => (
            <li key={e.kind} className="row">
              <span className="grow">{t.dynamic(`xp_${e.kind}`, "xp_base")}</span>
              <span>+{e.amount}</span>
            </li>
          ))}
        </ul>
      )}
      {rewards.note && <p className="muted small">{t(NOTES[rewards.note])}</p>}
    </section>
  );
}
