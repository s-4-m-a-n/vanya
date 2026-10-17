import { openDB, type DBSchema, type IDBPDatabase } from "idb";

import type { Observation, QueuedCapture } from "./types";

interface Schema extends DBSchema {
  /** Captures not yet accepted by the server. */
  queue: { key: string; value: QueuedCapture };
  /** Last known copy of the user's server observations, for offline viewing. */
  observations: { key: string; value: Observation };
  /** Small JPEG data URLs keyed by client UUID, shown when photos can't load. */
  thumbnails: { key: string; value: string };
}

export type DhDb = IDBPDatabase<Schema>;

export function openDhDb(name = "digitalhunter"): Promise<DhDb> {
  return openDB<Schema>(name, 1, {
    upgrade(db) {
      db.createObjectStore("queue", { keyPath: "clientUuid" });
      db.createObjectStore("observations", { keyPath: "id" });
      db.createObjectStore("thumbnails");
    },
  });
}

let shared: Promise<DhDb> | null = null;

export function getDb(): Promise<DhDb> {
  shared ??= openDhDb();
  return shared;
}

/** Replace the cached observation list with the server's current copy. */
export async function replaceObservations(db: DhDb, observations: Observation[]): Promise<void> {
  const tx = db.transaction("observations", "readwrite");
  await tx.store.clear();
  await Promise.all(observations.map((o) => tx.store.put(o)));
  await tx.done;
}

/** Remove everything stored for the signed-in user (sign-out). */
export async function clearUserData(db: DhDb): Promise<void> {
  const tx = db.transaction(["queue", "observations", "thumbnails"], "readwrite");
  await Promise.all([
    tx.objectStore("queue").clear(),
    tx.objectStore("observations").clear(),
    tx.objectStore("thumbnails").clear(),
  ]);
  await tx.done;
}
