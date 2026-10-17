import type {
  AccessPurpose,
  ExactLocation,
  Leaderboard,
  Observation,
  Page,
  Progress,
  QueuedCapture,
  QueueItem,
  RejectReason,
  Taxon,
  TaxonGroup,
  TokenResponse,
  User,
} from "./types";

const SESSION_KEY = "dh.session";

export interface Session {
  token: string;
  user: User;
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage unavailable (private mode); the session lasts until reload.
  }
  currentToken = session?.token ?? null;
}

// Read lazily so importing this module has no storage side effects.
let currentToken: string | null | undefined;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
    message: string,
  ) {
    super(message);
  }
}

/** The request never reached the server (offline, DNS, CORS, aborted). */
export class NetworkError extends Error {}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (currentToken === undefined) currentToken = loadSession()?.token ?? null;
  if (currentToken) headers.set("Authorization", `Bearer ${currentToken}`);
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...init, headers });
  } catch (error) {
    throw new NetworkError(String(error));
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const code = detail && typeof detail === "object" && !Array.isArray(detail) ? detail.code : undefined;
    const message =
      typeof detail === "string" ? detail : (detail?.message ?? `Request failed (${response.status})`);
    throw new ApiError(response.status, code, message);
  }
  return body as T;
}

export function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  register: (body: { email: string; password: string; display_name: string; locale: string }) =>
    request<TokenResponse>("/auth/register", json("POST", body)),
  login: (body: { email: string; password: string }) =>
    request<TokenResponse>("/auth/login", json("POST", body)),
  me: () => request<User>("/auth/me"),
  updateMe: (body: {
    locale?: string;
    display_name?: string;
    location_consent?: boolean;
    review_taxon_groups?: TaxonGroup[];
    review_area?: { latitude: number; longitude: number; radius_km: number } | null;
    leaderboard_visible?: boolean;
  }) => request<User>("/auth/me", json("PATCH", body)),

  progress: () => request<Progress>("/me/progress"),
  leaderboard: (params: {
    period: Leaderboard["period"];
    near?: { latitude: number; longitude: number; radiusKm: number };
  }) => {
    const query = new URLSearchParams({ period: params.period });
    if (params.near) {
      query.set("latitude", String(params.near.latitude));
      query.set("longitude", String(params.near.longitude));
      query.set("radius_km", String(params.near.radiusKm));
    }
    return request<Leaderboard>(`/leaderboards?${query}`);
  },

  searchTaxa: (q: string, locale: string) =>
    request<Page<Taxon>>(`/taxa?${new URLSearchParams({ q, locale, limit: "20" })}`),

  uploadCapture: (capture: QueuedCapture) => {
    const form = new FormData();
    form.set("photo", capture.photo, capture.photoName);
    form.set("client_uuid", capture.clientUuid);
    if (capture.latitude !== undefined && capture.longitude !== undefined) {
      form.set("latitude", String(capture.latitude));
      form.set("longitude", String(capture.longitude));
    }
    if (capture.accuracyM !== undefined) form.set("accuracy_m", String(capture.accuracyM));
    if (capture.observedAt) form.set("observed_at", capture.observedAt);
    if (capture.habitat) form.set("habitat", capture.habitat);
    if (capture.notes) form.set("notes", capture.notes);
    if (capture.obscureLocation) form.set("obscure_location", "true");
    // Lets the server read EXIF times that were written without a timezone.
    form.set("utc_offset_minutes", String(-new Date().getTimezoneOffset()));
    return request<Observation>("/observations", { method: "POST", body: form });
  },
  myObservations: () => request<Page<Observation>>("/observations/mine?limit=200"),
  getObservation: (id: string) => request<Observation>(`/observations/${id}`),
  /** Role-gated and audit-logged on the server. */
  revealLocation: (id: string, purpose: AccessPurpose, note?: string) =>
    request<ExactLocation>(`/observations/${id}/exact-location`, json("POST", { purpose, note })),
  chooseSpecies: (id: string, taxonId: number) =>
    request<Observation>(`/observations/${id}/species`, json("PUT", { taxon_id: taxonId })),
  deleteObservation: (id: string) => request<void>(`/observations/${id}`, { method: "DELETE" }),

  reviewQueue: (params: { group?: TaxonGroup; anywhere?: boolean; limit?: number }) => {
    const query = new URLSearchParams({ limit: String(params.limit ?? 20) });
    if (params.group) query.set("group", params.group);
    if (params.anywhere) query.set("anywhere", "true");
    return request<Page<QueueItem>>(`/reviews/queue?${query}`);
  },
  submitReview: (
    id: string,
    body: { decision: "confirm" | "reject"; taxon_id?: number; reason?: RejectReason; comment?: string },
  ) => request<Observation>(`/reviews/${id}`, json("POST", body)),
};
