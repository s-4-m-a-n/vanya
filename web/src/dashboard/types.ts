import type { MultiPolygon } from "geojson";

import type { Geoprivacy, Habitat, RarityTier, TaxonGroup } from "../types";

export interface Org {
  id: string;
  name: string;
  kind: string;
  my_role: "admin" | "analyst";
}

export interface Region {
  id: number;
  name: string;
  geometry: MultiPolygon;
  area_km2: number;
}

export interface Member {
  user_id: string;
  display_name: string;
  email: string;
  role: "admin" | "analyst";
}

export interface TaxonRef {
  id: number;
  scientific_name: string;
  common_name: string | null;
  taxon_group: TaxonGroup;
  iucn_status: string;
}

export interface WatchEntry {
  taxon: TaxonRef;
  category: "invasive" | "priority";
  note: string | null;
}

export interface RecordItem {
  id: string;
  taxon: TaxonRef;
  observed_at: string;
  latitude: number;
  longitude: number;
  accuracy_m: number;
  geoprivacy: Geoprivacy;
  rarity_tier: RarityTier | null;
  habitat: string | null;
  photo_url: string;
}

export interface Summary {
  observations: number;
  species: number;
  observers: number;
  rare_finds: number;
  threatened: number;
  invasive: number;
  top_species: { taxon: TaxonRef; count: number }[];
}

export interface TrendPoint {
  month: string;
  observations: number;
  species: number;
  by_group: Partial<Record<TaxonGroup, number>>;
}

export interface ChangeItem {
  taxon: TaxonRef;
  previous: number;
  current: number;
  change_pct: number | null;
}

export interface Changes {
  period_start: string;
  period_end: string;
  increases: ChangeItem[];
  decreases: ChangeItem[];
}

export interface NewRecord {
  taxon: TaxonRef;
  first_seen: string;
  records: number;
}

export interface Filters {
  regionId: number | null;
  group: TaxonGroup | null;
  taxon: TaxonRef | null;
  days: number | null; // null = all time (ignored when a custom range is set)
  /** Custom date range (YYYY-MM-DD, inclusive); overrides `days` when either is set. */
  from: string | null;
  to: string | null;
  habitats: Habitat[];
  iucn: string[];
  rarity: RarityTier[];
  verification: "auto" | "expert" | null;
  watchlist: "invasive" | "priority" | null;
  protected: boolean | null;
}

export const EMPTY_FILTERS: Filters = {
  regionId: null, group: null, taxon: null, days: 90, from: null, to: null,
  habitats: [], iucn: [], rarity: [], verification: null, watchlist: null, protected: null,
};

export interface AlertItem {
  id: string;
  kind: "invasive" | "new_record" | "priority";
  payload: {
    region: { id: number; name: string };
    observation_id: string;
    taxon: TaxonRef;
    observed_at: string;
    location: { latitude: number; longitude: number; accuracy_m: number; precision: string };
    rarity_tier: string | null;
  };
  observed_at: string;
  verified_at: string | null;
  created_at: string;
  acknowledged_at: string | null;
}

export interface AlertSettings {
  notify_invasive: boolean;
  notify_new_record: boolean;
  notify_priority: boolean;
  webhook_url: string | null;
  has_webhook_secret: boolean;
}

export interface AnalystAnswer {
  id: string;
  question: string;
  plan: { interpretation: string; warnings: string[]; intent: string } | null;
  result: { columns: string[]; rows: Record<string, string | number | null>[]; period?: string[]; previous_period?: string[] } | null;
  answer: { answer: string; highlights: string[]; caveats: string[] } | null;
  model: string | null;
  latency_ms: number | null;
  created_at: string;
}

export interface Report {
  id: string;
  period_start: string;
  period_end: string;
  locale: string;
  content: {
    title: string;
    summary: string;
    sections: { heading: string; paragraphs: string[] }[];
    recommendations: string[];
  };
  model: string | null;
  created_at: string;
}
