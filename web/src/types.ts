export type Role = "observer" | "verifier" | "analyst" | "admin";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  locale: string;
  reputation: number;
  location_consent_at: string | null;
  review_taxon_groups: TaxonGroup[];
  review_latitude: number | null;
  review_longitude: number | null;
  review_radius_km: number | null;
  leaderboard_visible: boolean;
  created_at: string;
}

export type TaxonGroup = "animal" | "bird" | "plant";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Taxon {
  id: number;
  scientific_name: string;
  common_name: string | null;
  common_names: Record<string, string>;
  taxon_group: TaxonGroup;
  kingdom: string;
  class_name: string | null;
  family: string | null;
  iucn_status: string;
  gbif_key: number | null;
}

export interface Candidate {
  rank: number;
  scientific_name: string;
  common_name: string | null;
  confidence: number;
  in_scope: boolean;
  taxon: Taxon | null;
}

export const HABITATS = [
  "forest",
  "grassland",
  "wetland",
  "freshwater",
  "coastal_marine",
  "desert_scrub",
  "mountain",
  "farmland",
  "urban_garden",
  "other",
] as const;
export type Habitat = (typeof HABITATS)[number];

export type ObservationStatus = "needs_id" | "submitted" | "in_review" | "verified" | "rejected";

export const REJECT_REASONS = [
  "not_wild",
  "wrong_location_or_time",
  "duplicate_or_stock",
  "unidentifiable",
  "not_an_organism",
  "disturbance",
  "inappropriate",
] as const;
export type RejectReason = (typeof REJECT_REASONS)[number];

export interface Review {
  decision: "confirm" | "reject";
  taxon: Taxon | null;
  reason: RejectReason | null;
  comment: string | null;
  reviewer_name: string | null;
  created_at: string;
}

export interface Flag {
  code: string;
  severity: "hard" | "soft";
  detail: string | null;
}

export interface Verification {
  is_rare: boolean | null;
  required_reviews: number | null;
  review_count: number;
  method: "auto" | "expert" | null;
  decided_at: string | null;
  rejection_reason: RejectReason | null;
  reviews: Review[];
  /** Only filled in for reviewer roles. */
  reasons: string[];
  integrity_flags: Flag[];
  photo_flags: string[];
}

export interface Observation {
  id: string;
  client_uuid: string;
  status: ObservationStatus;
  taxon: Taxon | null;
  /** The observer's own pick; differs from `taxon` when experts corrected it. */
  observer_taxon: Taxon | null;
  /** Exact for the observer and open records; otherwise a generalised grid point. */
  latitude: number | null;
  longitude: number | null;
  accuracy_m: number | null;
  location_precision: "exact" | "generalised";
  geoprivacy: Geoprivacy | null;
  observer_geoprivacy: Geoprivacy;
  location_source: "device" | "exif";
  observed_at: string;
  created_at: string;
  habitat: Habitat | null;
  notes: string | null;
  photo_url: string;
  photo_width: number;
  photo_height: number;
  identification: {
    provider: string | null;
    model: string | null;
    is_organism: boolean | null;
    explanation: string | null;
    error: string | null;
    latency_ms: number | null;
    candidates: Candidate[];
  };
  verification: Verification;
  rewards: Rewards;
}

export type Geoprivacy = "open" | "obscured" | "hidden";

export const ACCESS_PURPOSES = ["verification", "research", "management", "enforcement", "other"] as const;
export type AccessPurpose = (typeof ACCESS_PURPOSES)[number];

export interface ExactLocation {
  latitude: number;
  longitude: number;
  accuracy_m: number | null;
  exif_latitude: number | null;
  exif_longitude: number | null;
}

export type RarityTier = "common" | "uncommon" | "rare" | "very_rare" | "exceptional";

export interface Rewards {
  xp: number;
  status: "provisional" | "confirmed" | null;
  entries: { kind: string; amount: number; status: string }[];
  rarity_score: number | null;
  rarity_tier: RarityTier | null;
  note: "repeat" | "restricted_area" | "daily_cap" | null;
}

export interface Progress {
  xp: number;
  pending_xp: number;
  level: { number: number; floor: number; next_at: number };
  reputation: number;
  verified: number;
  species: number;
  rare_finds: number;
  badges: { code: string; awarded_at: string }[];
  all_badges: string[];
  rarity_bonus_left_today: number;
  leaderboard_visible: boolean;
}

export interface Standing {
  rank: number;
  user_id: string;
  display_name: string;
  xp: number;
  level: number;
  is_me: boolean;
}

export interface Leaderboard {
  period: "week" | "month" | "all";
  scope: "global" | "nearby";
  radius_km: number | null;
  entries: Standing[];
  me: Standing | null;
}

export interface QueueItem extends Observation {
  observer: { display_name: string; reputation: number };
}

export interface Page<T> {
  items: T[];
  total: number;
}

/** A capture waiting in IndexedDB until it can be uploaded. */
export interface QueuedCapture {
  clientUuid: string;
  photo: Blob;
  photoName: string;
  latitude?: number;
  longitude?: number;
  accuracyM?: number;
  /** Omitted for gallery photos so the server uses the photo's EXIF time. */
  observedAt?: string;
  habitat?: Habitat;
  notes?: string;
  /** Never show this record's exact location publicly. */
  obscureLocation?: boolean;
  state: "queued" | "uploading" | "failed";
  attempts: number;
  /** Error code (e.g. "location_required") or message from the last attempt. */
  lastError?: string;
  createdAt: string;
}
