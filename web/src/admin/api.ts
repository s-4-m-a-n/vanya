import { json, request } from "../api";
import type { Role, Taxon } from "../types";

export interface Overview {
  users: number;
  users_by_role: Record<string, number>;
  new_users_7d: number;
  observations_by_status: Record<string, number>;
  verified_7d: number;
  awaiting_review: number;
  organisations: number;
  restricted_areas: number;
  protected_species: number;
  reveals_7d: number;
  daily: { day: string; submitted: number; verified: number }[];
  top_observers: { user_id: string; display_name: string; verified: number }[];
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  reputation: number;
  created_at: string;
  observations: number;
  verified: number;
  last_sighting_at: string | null;
}

export interface AdminOrg {
  id: string;
  name: string;
  kind: string;
  members: number;
  regions: number;
  admins: string[];
  created_at: string;
}

export type Sensitivity = "open" | "obscured" | "hidden";

export interface AdminTaxon {
  taxon: Taxon;
  sensitivity: Sensitivity | null;
  status_source: string | null;
  observations: number;
}

export interface RestrictedArea {
  id: number;
  name: string;
  reason: string | null;
  active: boolean;
  area_km2: number;
  created_at: string;
}

export interface AccessEntry {
  id: number;
  user_email: string;
  user_role: string;
  observation_id: string;
  taxon_id: number | null;
  geoprivacy: string | null;
  purpose: string;
  note: string | null;
  ip: string | null;
  created_at: string;
}

export const ORG_KINDS = ["ngo", "park", "agency", "research", "other"] as const;

export const adminApi = {
  overview: () => request<Overview>("/admin/overview"),
  users: (params: { q?: string; role?: string; limit: number; offset: number }) => {
    const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.q) query.set("q", params.q);
    if (params.role) query.set("role", params.role);
    return request<{ items: AdminUser[]; total: number }>(`/admin/users?${query}`);
  },
  setRole: (id: string, role: Role) => request<unknown>(`/admin/users/${id}/role`, json("PATCH", { role })),
  orgs: () => request<AdminOrg[]>("/admin/organisations"),
  createOrg: (name: string, kind: string) => request<unknown>("/orgs", json("POST", { name, kind })),
  addMember: (org: string, email: string, role: "admin" | "analyst") =>
    request<unknown>(`/orgs/${org}/members`, json("POST", { email, role })),
  taxa: (q: string, protectedOnly: boolean) => {
    const query = new URLSearchParams({ protected_only: String(protectedOnly) });
    if (q) query.set("q", q);
    return request<AdminTaxon[]>(`/admin/taxa?${query}`);
  },
  setSensitivity: (id: number, sensitivity: Sensitivity | null) =>
    request<unknown>(`/admin/taxa/${id}`, json("PATCH", { sensitivity })),
  areas: () => request<RestrictedArea[]>("/admin/restricted-areas"),
  addArea: (name: string, reason: string, geojson: unknown) =>
    request<RestrictedArea>("/admin/restricted-areas", json("POST", { name, reason: reason || null, geojson })),
  setAreaActive: (id: number, active: boolean) =>
    request<RestrictedArea>(`/admin/restricted-areas/${id}`, json("PATCH", { active })),
  accessLog: () => request<AccessEntry[]>("/admin/location-access?limit=200"),
};
