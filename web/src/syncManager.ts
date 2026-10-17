import { useSyncExternalStore } from "react";

import { api } from "./api";
import { getDb } from "./db";
import { syncQueue, type SyncOutcome } from "./sync";

const RETRY_INTERVAL_MS = 30_000;

/**
 * Runs the upload queue when the app starts, when connectivity returns, when the app
 * comes back to the foreground, and periodically. Components subscribe to a version
 * number that changes whenever local data may have changed.
 */
class SyncManager {
  private current: Promise<SyncOutcome | null> | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  private timer: ReturnType<typeof setInterval> | undefined;
  onAuthExpired: () => void = () => {};

  start(): void {
    if (this.timer) return;
    window.addEventListener("online", this.trigger);
    document.addEventListener("visibilitychange", this.onVisible);
    this.timer = setInterval(this.trigger, RETRY_INTERVAL_MS);
    void this.run();
  }

  stop(): void {
    window.removeEventListener("online", this.trigger);
    document.removeEventListener("visibilitychange", this.onVisible);
    clearInterval(this.timer);
    this.timer = undefined;
  }

  get running(): boolean {
    return this.current !== null;
  }

  /** Single-flight: concurrent callers share the same run. */
  run(): Promise<SyncOutcome | null> {
    if (this.current) return this.current;
    if (!navigator.onLine) return Promise.resolve(null);
    this.current = (async () => {
      this.notify();
      try {
        const outcome = await syncQueue(await getDb(), api.uploadCapture);
        if (outcome.authExpired) this.onAuthExpired();
        return outcome;
      } finally {
        this.current = null;
        this.notify();
      }
    })();
    return this.current;
  }

  notify = (): void => {
    this.version++;
    this.listeners.forEach((listener) => listener());
  };

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = (): number => this.version;

  private trigger = (): void => {
    void this.run();
  };

  private onVisible = (): void => {
    if (document.visibilityState === "visible") void this.run();
  };
}

export const syncManager = new SyncManager();

/** Re-render when queued or cached data may have changed. */
export function useDataVersion(): number {
  return useSyncExternalStore(syncManager.subscribe, syncManager.getVersion);
}

function subscribeOnline(callback: () => void): () => void {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine);
}

// Dev only: a hot-reloaded module must not leave the previous instance syncing.
import.meta.hot?.dispose(() => syncManager.stop());
