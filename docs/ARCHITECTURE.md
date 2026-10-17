# Architecture: local now, Google Cloud later

The app runs fully locally on open-source tools. Each Google Cloud service from PRD §9 sits
behind a **port** (an abstract interface in `backend/app/adapters/`). A `*_PROVIDER` env var picks
the implementation, so moving to GCP is configuration plus infrastructure, not a rewrite.

## Service mapping

| PRD layer | GCP service | Local stand-in | Switch | Status |
|---|---|---|---|---|
| API, workers | Cloud Run | Same Docker image via docker compose | deploy image | ✅ Stage 0 |
| Operational DB (users, observations, queues) | Cloud SQL for PostgreSQL 16 + PostGIS | `postgis/postgis:16-3.5` container | `DATABASE_URL` | ✅ Stage 0 |
| Photos | Cloud Storage (V4 signed URLs) | Local disk + HMAC-signed `/media` URLs | `STORAGE_PROVIDER=gcs`, `GCS_BUCKET` | ✅ Stage 0 |
| Alerts and workflows | Pub/Sub (push subscriptions → Cloud Run) | In-process event bus on a background thread pool | `EVENTS_PROVIDER=pubsub` | ✅ Stages 0, 2 (push endpoint `/internal/events/pubsub`) |
| Species ID | Vertex AI vision model; Gemini as fallback/explainer | Gemini 3 Flash via OpenRouter (or a deterministic stub) | `SPECIES_ID_PROVIDER=llm`, `LLM_PROVIDER=vertex` | ✅ Stage 1 |
| Analytics, rarity, trends | BigQuery (`<dataset>.observations`, streamed on verification) | Postgres queries over `observations` | `ANALYTICS_PROVIDER=bigquery`, `BIGQUERY_DATASET` | ✅ Stage 3 (dashboards: Stage 5) |
| Scheduled jobs | Cloud Scheduler → Cloud Run Jobs: `recompute-rarity` (daily), `route-pending` (hourly), `refresh-statuses` (weekly), `generate-reports --days 30` (monthly) | Run the CLI (or cron) | deploy | ✅ Stages 3–6 |
| Reports, NL queries | Gemini on Vertex AI | Gemini 3 Flash via OpenRouter | `LLM_PROVIDER=vertex` | ✅ Stage 6 |
| Alert delivery | Pub/Sub `dh.alert.created` → webhook handler (retries) | In-process event → webhook handler | `EVENTS_PROVIDER=pubsub` | ✅ Stage 6 |
| Taxonomy validation | — (same in both) | GBIF backbone API (open data, no key) | `TAXONOMY_RESOLVER` | ✅ Stage 1 |
| Conservation status | — (same in both) | IUCN Red List category via GBIF | `OCCURRENCE_BASELINE=gbif` | ✅ Stage 4 |
| Regional commonness baseline | GBIF API (or a GBIF snapshot in BigQuery later) | GBIF occurrence counts, cached in Postgres | `OCCURRENCE_BASELINE` | ✅ Stage 2 |
| Stuck-submission sweeper | Cloud Scheduler → Cloud Run Job `python -m app.cli route-pending` | Run the CLI by hand | deploy | ✅ Stage 2 |
| Citizen app hosting | Firebase Hosting or Cloud Run (static) | Vite dev server / `vite preview` | deploy `web/dist` | ✅ Stage 1 |
| Environmental context (P1 F8) | Earth Engine | — (post-MVP) | — | Later |
| Maps | Tile provider of choice (or Google Maps Platform) | Leaflet + OpenStreetMap public tiles (dev only, per OSM tile policy) | tile URL | ✅ Stage 5 |
| Organisation dashboard queries | BigQuery (via the analytics port) | Postgres (`services/dashboard.py`) | `ANALYTICS_PROVIDER` | ✅ Stage 5 on Postgres; BigQuery port pending |
| Auth | (optional) Identity Platform | Built-in JWT + Argon2 | — | ✅ Stage 0 |

**Why Postgres and BigQuery both?** BigQuery is an analytics warehouse, not a transactional
store. Observations, users and verification queues live in Postgres (Cloud SQL on GCP), and
verified records are exported to BigQuery for rarity, trends and Gemini queries. Locally, the
analytics port runs the same queries on Postgres.

## How a port works

```
app/adapters/storage/
  base.py      BlobStorage (abstract)  ← application code depends only on this
  local.py     LocalBlobStorage        ← default
  gcs.py       GcsBlobStorage          ← STORAGE_PROVIDER=gcs (needs the `gcp` extra)
  __init__.py  get_storage()           ← picks the implementation from settings
```

Events use the same pattern, plus a shared handler registry (`@subscribe("topic")`, see
`app/handlers.py`). Locally the in-process bus runs handlers on a background thread pool. On
GCP, each topic gets a push subscription to `POST /internal/events/pubsub`. That endpoint verifies
the Google-signed OIDC token (`PUBSUB_PUSH_AUDIENCE`, `PUBSUB_PUSH_SERVICE_ACCOUNT`) and calls the
same handlers. A handler error returns 500, so Pub/Sub retries with backoff.

The LLM port follows the same pattern: `OpenRouterClient` and `VertexGeminiClient` both expose
`generate_json(system, prompt, schema, images)`. One `LLM_MODEL` setting serves both; the Vertex
adapter drops OpenRouter's `google/` prefix. Moving to GCP means setting `LLM_PROVIDER=vertex`.

GCP adapters are covered by contract tests with the SDKs mocked (`tests/test_gcp_adapters.py`).
They'll be exercised for real once a project exists.

## Data flow (PRD §9)

capture → upload → ID → integrity checks → verification routing → Postgres (+ export to
BigQuery) → rarity recompute → dashboard, alerts and reports

## What I'll need from you for GCP

None of this is needed until Stage 7, but the list is here so you can prepare:

1. **Project ID and region** (e.g. `asia-south1` or `europe-west1`), with billing enabled.
2. **APIs enabled:** Cloud Run, Cloud SQL Admin, Cloud Storage, Pub/Sub, BigQuery, Vertex AI,
   Artifact Registry, Secret Manager, Cloud Scheduler (plus Earth Engine when F8 starts).
3. **Cloud SQL:** a PostgreSQL 16 instance, plus a database, a user and the instance connection name.
4. **Cloud Storage:** a private bucket (uniform access) for photos.
5. **Artifact Registry:** a Docker repository for the API image.
6. **Runtime service account** with: `roles/cloudsql.client`, `roles/storage.objectAdmin` (on the
   bucket), `roles/pubsub.publisher`, `roles/bigquery.dataEditor` + `roles/bigquery.jobUser`,
   `roles/aiplatform.user`, `roles/secretmanager.secretAccessor`, and
   `roles/iam.serviceAccountTokenCreator` on itself (needed to sign photo URLs).
7. **Secret Manager entries:** `JWT_SECRET`, the DB password, and (later) organisations' webhook signing secrets.
8. **Pub/Sub:** topics `dh.observation.submitted`, `dh.observation.in_review`, `dh.observation.verified`,
   `dh.observation.rejected`, `dh.observation.deleted` and `dh.alert.created` (I'll script these), each with a push
   subscription to `https://<api>/internal/events/pubsub`, using an invoker service account whose
   email goes in `PUBSUB_PUSH_SERVICE_ACCOUNT`.
9. **BigQuery:** a dataset name and location (same region as above). I'll create the
   `observations` table (schema in `app/adapters/analytics/bigquery.py`).
10. **Vertex AI:** confirm Gemini 3 Flash access in the chosen region (`GCP_LOCATION`, `global` for preview models). Until then, AI runs through your OpenRouter key.
11. **Deployment:** whether you'll run the deploy commands I provide, or set up a deployer
    service account or CI. I won't need your personal credentials.
