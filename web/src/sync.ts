import { ApiError, NetworkError } from "./api";
import type { DhDb } from "./db";
import type { Observation, QueuedCapture } from "./types";

export type Uploader = (capture: QueuedCapture) => Promise<Observation>;

export interface SyncOutcome {
  uploaded: Observation[];
  failed: QueuedCapture[];
  /** Captures still waiting (offline or transient server errors). */
  pending: number;
  authExpired: boolean;
}

// Client errors worth retrying later rather than showing as permanent failures.
const TRANSIENT_STATUSES = new Set([408, 425, 429]);

/**
 * Upload queued captures oldest-first. Uploads are idempotent on the server (keyed by
 * clientUuid), so retrying after an interrupted request never creates duplicates.
 */
export async function syncQueue(db: DhDb, upload: Uploader): Promise<SyncOutcome> {
  const outcome: SyncOutcome = { uploaded: [], failed: [], pending: 0, authExpired: false };
  const items = (await db.getAll("queue"))
    .filter((item) => item.state !== "failed")
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));

  for (const item of items) {
    await db.put("queue", { ...item, state: "uploading" });
    try {
      const observation = await upload(item);
      const tx = db.transaction(["queue", "observations"], "readwrite");
      await Promise.all([
        tx.objectStore("queue").delete(item.clientUuid),
        tx.objectStore("observations").put(observation),
        tx.done,
      ]);
      outcome.uploaded.push(observation);
    } catch (error) {
      const attempts = item.attempts + 1;
      if (error instanceof ApiError && error.status === 401) {
        await db.put("queue", { ...item, state: "queued" });
        outcome.authExpired = true;
        break;
      }
      if (
        error instanceof ApiError &&
        error.status < 500 &&
        !TRANSIENT_STATUSES.has(error.status)
      ) {
        const failed: QueuedCapture = {
          ...item,
          state: "failed",
          attempts,
          lastError: error.code ?? (error.status === 413 ? "too_large" : error.message),
        };
        await db.put("queue", failed);
        outcome.failed.push(failed);
        continue;
      }
      const message = error instanceof Error ? error.message : String(error);
      await db.put("queue", { ...item, state: "queued", attempts, lastError: message });
      // Offline: stop here and wait for connectivity instead of failing every item.
      if (error instanceof NetworkError) break;
    }
  }

  outcome.pending = (await db.getAll("queue")).filter((i) => i.state !== "failed").length;
  return outcome;
}

/** Put a failed capture back in line (e.g. after the user fixes GPS or consent). */
export async function retryCapture(db: DhDb, clientUuid: string): Promise<void> {
  const item = await db.get("queue", clientUuid);
  if (item) await db.put("queue", { ...item, state: "queued", lastError: undefined });
}
