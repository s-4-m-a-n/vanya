import { describe, expect, it } from "vitest";

import { awaitingServer } from "./pending";
import type { Observation } from "./types";

const rewards = (status: string | null, note: string | null = null) =>
  ({ xp: 0, status, entries: [], rarity_score: null, rarity_tier: null, note }) as Observation["rewards"];
const obs = (status: Observation["status"], r = rewards(null)) => ({ status, rewards: r }) as Observation;

describe("awaitingServer", () => {
  it("waits for routing, then for the XP calculation", () => {
    expect(awaitingServer(obs("submitted"))).toBe(true);
    expect(awaitingServer(obs("verified"))).toBe(true);
    expect(awaitingServer(obs("in_review"))).toBe(true);
  });

  it("stops once XP is known, withheld, or not applicable", () => {
    expect(awaitingServer(obs("verified", rewards("confirmed")))).toBe(false);
    expect(awaitingServer(obs("in_review", rewards("provisional")))).toBe(false);
    expect(awaitingServer(obs("verified", rewards(null, "restricted_area")))).toBe(false);
    expect(awaitingServer(obs("rejected"))).toBe(false);
    expect(awaitingServer(obs("needs_id"))).toBe(false);
  });
});
