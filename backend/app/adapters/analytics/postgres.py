from typing import Any

from sqlalchemy import func, select

from app.adapters.analytics.base import AnalyticsStore, CellCount
from app.db import get_sessionmaker
from app.models import Observation, ObservationStatus


class PostgresAnalyticsStore(AnalyticsStore):
    """Queries the operational database directly; nothing to export or forget."""

    def species_cells(self, step: float) -> list[CellCount]:
        lat_cell = func.floor(Observation.latitude / step)
        lon_cell = func.floor(Observation.longitude / step)
        stmt = (
            select(lat_cell, lon_cell, Observation.taxon_id, func.count())
            .where(Observation.status == ObservationStatus.VERIFIED.value)
            .group_by(lat_cell, lon_cell, Observation.taxon_id)
        )
        with get_sessionmaker()() as db:
            return [
                CellCount((lat + 0.5) * step, (lon + 0.5) * step, taxon_id, count)
                for lat, lon, taxon_id, count in db.execute(stmt)
            ]

    def record_verified(self, row: dict[str, Any]) -> None:
        pass

    def forget(self, observation_id: str) -> None:
        pass
