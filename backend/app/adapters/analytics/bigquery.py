from typing import Any

from app.adapters.analytics.base import AnalyticsStore, CellCount

# Table `<project>.<dataset>.observations` (one row per verified record):
#   observation_id STRING, taxon_id INT64, scientific_name STRING, taxon_group STRING,
#   iucn_status STRING, latitude FLOAT64, longitude FLOAT64, observed_at TIMESTAMP,
#   verified_at TIMESTAMP, method STRING, is_rare BOOL, rarity_score FLOAT64, habitat STRING
# No user identifiers: analytics doesn't need them (PRD §8, minimal personal data).


class BigQueryAnalyticsStore(AnalyticsStore):
    """BigQuery. Requires the `gcp` extra and roles/bigquery.dataEditor + jobUser."""

    def __init__(self, project: str, dataset: str) -> None:
        from google.cloud import bigquery

        self._bq = bigquery
        self._client = bigquery.Client(project=project)
        self._table = f"{project}.{dataset}.observations"

    def species_cells(self, step: float) -> list[CellCount]:
        sql = f"""
            SELECT FLOOR(latitude / @step) AS lat_cell, FLOOR(longitude / @step) AS lon_cell,
                   taxon_id, COUNT(*) AS n
            FROM `{self._table}`
            GROUP BY lat_cell, lon_cell, taxon_id
        """
        config = self._bq.QueryJobConfig(
            query_parameters=[self._bq.ScalarQueryParameter("step", "FLOAT64", step)]
        )
        rows = self._client.query(sql, job_config=config).result()
        return [
            CellCount(
                (r["lat_cell"] + 0.5) * step, (r["lon_cell"] + 0.5) * step, r["taxon_id"], r["n"]
            )
            for r in rows
        ]

    def record_verified(self, row: dict[str, Any]) -> None:
        errors = self._client.insert_rows_json(self._table, [row], row_ids=[row["observation_id"]])
        if errors:
            raise RuntimeError(f"BigQuery insert failed: {errors}")

    def forget(self, observation_id: str) -> None:
        config = self._bq.QueryJobConfig(
            query_parameters=[self._bq.ScalarQueryParameter("id", "STRING", observation_id)]
        )
        self._client.query(
            f"DELETE FROM `{self._table}` WHERE observation_id = @id", job_config=config
        ).result()
