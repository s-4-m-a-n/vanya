from functools import lru_cache

from app.adapters.analytics.base import AnalyticsStore, CellCount
from app.config import get_settings


@lru_cache
def get_analytics() -> AnalyticsStore:
    settings = get_settings()
    if settings.analytics_provider == "bigquery":
        from app.adapters.analytics.bigquery import BigQueryAnalyticsStore

        if not (settings.gcp_project_id and settings.bigquery_dataset):
            raise RuntimeError("GCP_PROJECT_ID and BIGQUERY_DATASET are required for BigQuery")
        return BigQueryAnalyticsStore(settings.gcp_project_id, settings.bigquery_dataset)

    from app.adapters.analytics.postgres import PostgresAnalyticsStore

    return PostgresAnalyticsStore()


__all__ = ["AnalyticsStore", "CellCount", "get_analytics"]
