"""Runtime configuration.

Every Google Cloud dependency sits behind a provider switch. Local development uses the
open-source defaults below; migrating to GCP means changing env vars, not code
(see docs/ARCHITECTURE.md).
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Product name used in API docs and outgoing request headers. Internal identifiers
# (database names, event topics, storage keys) keep the "digitalhunter"/"dh" codename.
APP_NAME = "Vanya"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    # Comma-separated in env: CORS_ORIGINS=http://localhost:5173,http://localhost:5174
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:5174"]
    )

    # Postgres + PostGIS locally; Cloud SQL for PostgreSQL on GCP (same URL format,
    # e.g. postgresql+psycopg://user:pass@/db?host=/cloudsql/PROJECT:REGION:INSTANCE).
    database_url: str = (
        "postgresql+psycopg://digitalhunter:digitalhunter@localhost:5433/digitalhunter"
    )

    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 60 * 24

    max_upload_mb: int = 15

    # --- Provider switches -------------------------------------------------------------
    storage_provider: Literal["local", "gcs"] = "local"
    storage_local_dir: str = "./var/media"
    # Origin prefixed to local media URLs. Empty means same-origin relative URLs, which
    # works behind the web dev proxy and on phones over the LAN.
    media_base_url: str = ""
    # Signs local media URLs, standing in for GCS V4 signed URLs.
    media_url_secret: str = "change-me-media-secret"
    gcs_bucket: str | None = None

    events_provider: Literal["inprocess", "pubsub"] = "inprocess"
    # Run in-process handlers on background threads, like Pub/Sub delivery on GCP, so slow
    # consumers (e.g. GBIF lookups during routing) never hold up API responses.
    events_inprocess_async: bool = True
    pubsub_topic_prefix: str = "dh."

    # Gemini via OpenRouter locally; Gemini on Vertex AI on GCP. LLM_MODEL takes the
    # OpenRouter slug; the Vertex adapter drops the "google/" prefix.
    llm_provider: Literal["none", "openrouter", "vertex"] = "none"
    llm_model: str = "google/gemini-3-flash-preview"
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    # Caps each response (reasoning included). Without it OpenRouter reserves credit for the
    # model's full output window on every call.
    llm_max_output_tokens: int = 4096
    llm_timeout_seconds: float = 45.0
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # `llm` identifies species with the configured LLM (Gemini). A custom Vertex AI vision
    # endpoint (PRD §9) can be added as another provider later.
    species_id_provider: Literal["stub", "llm"] = "stub"
    species_id_top_k: int = 3

    # `gbif` validates names the identifier returns against the GBIF backbone and adds
    # new in-scope species to the taxa table; `local` only matches existing taxa.
    taxonomy_resolver: Literal["local", "gbif"] = "local"
    gbif_api_url: str = "https://api.gbif.org/v1"
    # GBIF can take several seconds. Uploads wait on name lookups, so keep those short;
    # verification routing runs in the background and can afford to wait.
    gbif_timeout_seconds: float = 5.0
    gbif_background_timeout_seconds: float = 20.0

    # --- Verification (PRD F3, §6) ---
    # `gbif` uses GBIF occurrence counts as the regional commonness baseline; `none` treats
    # commonness as unknown, so nothing is auto-accepted.
    occurrence_baseline: Literal["none", "gbif"] = "none"
    occurrence_cache_days: int = 30
    auto_accept_min_confidence: float = 0.85
    # "Common here" if GBIF has this many records within ±0.5° (~55 km)...
    common_min_gbif_records: int = 20
    # ...or we have verified this many within 25 km.
    common_min_verified_records: int = 5
    location_mismatch_km: float = 5.0
    time_mismatch_hours: float = 24.0
    near_duplicate_max_bits: int = 5

    # Analytical queries (rarity recompute, later dashboards): Postgres locally,
    # BigQuery on GCP, where verified records are streamed as they're verified.
    analytics_provider: Literal["postgres", "bigquery"] = "postgres"
    bigquery_dataset: str | None = None

    gcp_project_id: str | None = None
    gcp_location: str = "global"
    # Pub/Sub push subscriptions call /internal/events/pubsub with a Google-signed OIDC token.
    pubsub_push_audience: str | None = None
    pubsub_push_service_account: str | None = None

    @model_validator(mode="after")
    def _require_real_secrets_outside_local(self) -> "Settings":
        if self.app_env in ("staging", "production"):
            weak = [
                name
                for name in ("jwt_secret", "media_url_secret")
                if getattr(self, name).startswith("change-me") or len(getattr(self, name)) < 32
            ]
            if weak:
                raise ValueError(f"Set strong values (>=32 chars) for: {', '.join(weak)}")
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
