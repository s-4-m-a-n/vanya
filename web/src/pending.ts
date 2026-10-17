import type { Observation } from "./types";

/**
 * True while background work for a sighting is still running: verification after
 * submission, then the XP calculation once it's in review or verified. Screens keep
 * polling until this turns false.
 */
export function awaitingServer(obs: Observation): boolean {
  if (obs.status === "submitted") return true;
  if (obs.status !== "in_review" && obs.status !== "verified") return false;
  return !obs.rewards?.status && !obs.rewards?.note;
}
