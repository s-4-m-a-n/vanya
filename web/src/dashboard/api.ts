import type { FeatureCollection, Point, Polygon } from "geojson";

import { json, loadSession, request } from "../api";
import type { TaxonGroup } from "../types";
import type {
  AlertItem,
  AlertSettings,
  AnalystAnswer,
  Changes,
  Filters,
  Member,
  NewRecord,
  Org,
  RecordItem,
  Region,
  Report,
  Summary,
  TrendPoint,
  WatchEntry,
} from "./types";

function scope(filters: Filters): string {
  const query = new URLSearchParams();
  if (filters.regionId != null) query.set("region_id", String(filters.regionId));
  if (filters.group) query.set("group", filters.group);
  if (filters.taxon) query.set("taxon_id", String(filters.taxon.id));
  filters.habitats.forEach((h) => query.append("habitat", h));
  filters.iucn.forEach((s) => query.append("iucn", s));
  filters.rarity.forEach((r) => query.append("rarity", r));
  if (filters.verification) query.set("verification", filters.verification);
  if (filters.watchlist) query.set("watchlist", filters.watchlist);
  if (filters.protected != null) query.set("protected", String(filters.protected));
  if (filters.from || filters.to) {
    if (filters.from) query.set("start", filters.from);
    if (filters.to) query.set("end", filters.to);
  } else if (filters.days != null) {
    const start = new Date(Date.now() - filters.days * 86_400_000);
    query.set("start", start.toISOString().slice(0, 10));
    query.set("end", new Date().toISOString().slice(0, 10));
  }
  return query.toString();
}

export const dashboardApi = {
  orgs: () => request<Org[]>("/orgs"),
  members: (org: string) => request<Member[]>(`/orgs/${org}/members`),
  addMember: (org: string, email: string, role: Member["role"]) =>
    request<Member>(`/orgs/${org}/members`, json("POST", { email, role })),
  removeMember: (org: string, userId: string) =>
    request<void>(`/orgs/${org}/members/${userId}`, { method: "DELETE" }),
  regions: (org: string) => request<Region[]>(`/orgs/${org}/regions`),
  addRegion: (org: string, name: string, geometry: unknown) =>
    request<Region>(`/orgs/${org}/regions`, json("POST", { name, geometry })),
  removeRegion: (org: string, id: number) =>
    request<void>(`/orgs/${org}/regions/${id}`, { method: "DELETE" }),
  watchlist: (org: string) => request<WatchEntry[]>(`/orgs/${org}/watchlist`),
  watch: (org: string, taxonId: number, category: WatchEntry["category"]) =>
    request<WatchEntry>(`/orgs/${org}/watchlist/${taxonId}`, json("PUT", { category })),
  unwatch: (org: string, taxonId: number) =>
    request<void>(`/orgs/${org}/watchlist/${taxonId}`, { method: "DELETE" }),

  summary: (org: string, f: Filters) => request<Summary>(`/orgs/${org}/dashboard/summary?${scope(f)}`),
  points: (org: string, f: Filters) =>
    request<FeatureCollection<Point, RecordItem>>(
      `/orgs/${org}/dashboard/observations?${scope(f)}`,
    ),
  grid: (org: string, f: Filters, step: number) =>
    request<FeatureCollection<Polygon, { observations: number; species: number }>>(
      `/orgs/${org}/dashboard/grid?${scope(f)}&step=${step}`,
    ),
  trends: (org: string, f: Filters) => request<TrendPoint[]>(`/orgs/${org}/dashboard/trends?${scope(f)}`),
  changes: (org: string, f: Filters) => request<Changes>(`/orgs/${org}/dashboard/changes?${scope(f)}`),
  rare: (org: string, f: Filters) => request<RecordItem[]>(`/orgs/${org}/dashboard/rare?${scope(f)}`),
  invasive: (org: string, f: Filters) => request<RecordItem[]>(`/orgs/${org}/dashboard/invasive?${scope(f)}`),
  exportCsv: async (org: string, f: Filters): Promise<Blob> => {
    const token = loadSession()?.token;
    const res = await fetch(`/api/v1/orgs/${org}/dashboard/export.csv?${scope(f)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    return res.blob();
  },
  newRecords: (org: string, f: Filters) => request<NewRecord[]>(`/orgs/${org}/dashboard/new-records?${scope(f)}`),

  alerts: (org: string, unreadOnly: boolean) =>
    request<{ items: AlertItem[]; unread: number }>(`/orgs/${org}/alerts?unread_only=${unreadOnly}`),
  acknowledge: (org: string, id: string) =>
    request<AlertItem>(`/orgs/${org}/alerts/${id}/acknowledge`, { method: "POST" }),
  alertSettings: (org: string) => request<AlertSettings>(`/orgs/${org}/alert-settings`),
  saveAlertSettings: (org: string, body: Partial<AlertSettings>) =>
    request<AlertSettings>(`/orgs/${org}/alert-settings`, json("PUT", body)),
  rotateSecret: (org: string) =>
    request<{ webhook_secret: string }>(`/orgs/${org}/alert-settings/rotate-secret`, { method: "POST" }),
  testWebhook: (org: string) =>
    request<{ ok: boolean; http_status: number | null; error: string | null }>(
      `/orgs/${org}/alert-settings/test-webhook`,
      { method: "POST" },
    ),

  ask: (org: string, question: string) =>
    request<AnalystAnswer>(`/orgs/${org}/analyst/ask`, json("POST", { question })),
  history: (org: string) => request<AnalystAnswer[]>(`/orgs/${org}/analyst/history`),
  reports: (org: string) => request<Report[]>(`/orgs/${org}/reports`),
  createReport: (org: string, period: "week" | "month" | "quarter") =>
    request<Report>(`/orgs/${org}/reports`, json("POST", { period })),
};

export const GROUPS: TaxonGroup[] = ["bird", "animal", "plant"];
