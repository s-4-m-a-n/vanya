import "fake-indexeddb/auto";

import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "./api";
import { openDhDb, type DhDb } from "./db";
import { retryCapture, syncQueue } from "./sync";
import type { Observation, QueuedCapture } from "./types";

let db: DhDb;
let dbCounter = 0;

function capture(id: string, createdAt: string, extra: Partial<QueuedCapture> = {}): QueuedCapture {
  return {
    clientUuid: id,
    photo: new Blob(["jpeg"], { type: "image/jpeg" }),
    photoName: `${id}.jpg`,
    latitude: 1,
    longitude: 2,
    state: "queued",
    attempts: 0,
    createdAt,
    ...extra,
  };
}

function observation(clientUuid: string): Observation {
  return { id: `server-${clientUuid}`, client_uuid: clientUuid } as Observation;
}

beforeEach(async () => {
  db = await openDhDb(`test-${++dbCounter}`);
});

describe("syncQueue", () => {
  it("uploads oldest first and moves results into the observation cache", async () => {
    await db.put("queue", capture("b", "2026-10-02T00:00:00Z"));
    await db.put("queue", capture("a", "2026-10-01T00:00:00Z"));
    const order: string[] = [];

    const outcome = await syncQueue(db, async (c) => {
      order.push(c.clientUuid);
      return observation(c.clientUuid);
    });

    expect(order).toEqual(["a", "b"]);
    expect(outcome.uploaded.map((o) => o.id)).toEqual(["server-a", "server-b"]);
    expect(outcome.pending).toBe(0);
    expect(await db.count("queue")).toBe(0);
    expect(await db.get("observations", "server-a")).toBeTruthy();
  });

  it("stops at the first network error and keeps everything queued", async () => {
    await db.put("queue", capture("a", "2026-10-01T00:00:00Z"));
    await db.put("queue", capture("b", "2026-10-02T00:00:00Z"));
    let calls = 0;

    const outcome = await syncQueue(db, async () => {
      calls++;
      throw new NetworkError("offline");
    });

    expect(calls).toBe(1);
    expect(outcome.pending).toBe(2);
    const a = await db.get("queue", "a");
    expect(a?.state).toBe("queued");
    expect(a?.attempts).toBe(1);
  });

  it("marks validation errors as failed with the error code and carries on", async () => {
    await db.put("queue", capture("bad", "2026-10-01T00:00:00Z"));
    await db.put("queue", capture("good", "2026-10-02T00:00:00Z"));

    const outcome = await syncQueue(db, async (c) => {
      if (c.clientUuid === "bad") throw new ApiError(422, "location_required", "No location");
      return observation(c.clientUuid);
    });

    expect(outcome.failed.map((f) => f.lastError)).toEqual(["location_required"]);
    expect(outcome.uploaded).toHaveLength(1);
    expect(outcome.pending).toBe(0);
    expect((await db.get("queue", "bad"))?.state).toBe("failed");
  });

  it("keeps items queued on server errors and rate limits", async () => {
    await db.put("queue", capture("a", "2026-10-01T00:00:00Z"));
    await db.put("queue", capture("b", "2026-10-02T00:00:00Z"));

    const outcome = await syncQueue(db, async (c) => {
      throw new ApiError(c.clientUuid === "a" ? 503 : 429, undefined, "busy");
    });

    expect(outcome.failed).toHaveLength(0);
    expect(outcome.pending).toBe(2);
  });

  it("stops and reports when the session has expired", async () => {
    await db.put("queue", capture("a", "2026-10-01T00:00:00Z"));
    const outcome = await syncQueue(db, async () => {
      throw new ApiError(401, undefined, "Not authenticated");
    });
    expect(outcome.authExpired).toBe(true);
    expect((await db.get("queue", "a"))?.state).toBe("queued");
  });

  it("retries captures interrupted mid-upload and skips failed ones", async () => {
    await db.put("queue", capture("stuck", "2026-10-01T00:00:00Z", { state: "uploading" }));
    await db.put("queue", capture("failed", "2026-10-02T00:00:00Z", { state: "failed" }));
    const seen: string[] = [];

    await syncQueue(db, async (c) => {
      seen.push(c.clientUuid);
      return observation(c.clientUuid);
    });

    expect(seen).toEqual(["stuck"]);
  });

  it("retryCapture puts a failed capture back in line", async () => {
    await db.put("queue", capture("f", "2026-10-01T00:00:00Z", { state: "failed", lastError: "x" }));
    await retryCapture(db, "f");
    const item = await db.get("queue", "f");
    expect(item?.state).toBe("queued");
    expect(item?.lastError).toBeUndefined();
  });
});
