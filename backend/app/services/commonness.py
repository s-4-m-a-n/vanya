"""How common a species is around a point: the regional baseline for verification routing.

Combines GBIF occurrence counts (open data, cached per area) with our own verified records.
Stage 3 builds the full rarity score on top of this.
"""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Observation, ObservationStatus, OccurrenceCount, Taxon
from app.services.gbif import GbifMatch

logger = logging.getLogger(__name__)

LOCAL_STEP_DEG, LOCAL_BOX_DEG, LOCAL_RADIUS_M = 0.5, 0.5, 25_000
RANGE_STEP_DEG, RANGE_BOX_DEG, RANGE_RADIUS_M = 2.5, 2.5, 250_000


def cell_center(lat: float, lon: float, step: float = LOCAL_STEP_DEG) -> tuple[float, float]:
    """Centre of the grid cell containing the point.

    Uses floor, not round: Python rounds halves to even while Postgres and BigQuery round
    them away from zero, so round() would put boundary points in different cells.
    """
    return (math.floor(lat / step) + 0.5) * step, (math.floor(lon / step) + 0.5) * step


def cell_key(lat: float, lon: float, step: float = LOCAL_STEP_DEG) -> str:
    """Grid cell id, e.g. "27.50:85.50" for the ~55 km cell containing the point."""
    center_lat, center_lon = cell_center(lat, lon, step)
    return f"{center_lat:.2f}:{center_lon:.2f}"


class Commonness(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"  # recorded nearby, but rarely
    OUT_OF_RANGE = "out_of_range"  # no records anywhere in the wider region
    UNKNOWN = "unknown"  # no baseline available (GBIF off or unreachable)


class OccurrenceSource(Protocol):
    def match_species(self, name: str) -> GbifMatch | None: ...

    def count_occurrences(
        self,
        taxon_key: int | list[int],
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
    ) -> int | None: ...

    def top_species_count(
        self, taxon_keys: list[int], lat_min: float, lat_max: float, lon_min: float, lon_max: float
    ) -> int | None: ...

    def country_count(self, taxon_key: int, min_records: int = 3) -> int | None: ...

    def iucn_category(self, species_key: int) -> str | None: ...


class CommonnessService:
    def __init__(
        self,
        db: Session,
        source: OccurrenceSource | None,
        min_gbif_records: int,
        min_verified_records: int,
        cache_days: int,
    ) -> None:
        self.db = db
        self.source = source
        self.min_gbif = min_gbif_records
        self.min_verified = min_verified_records
        self.cache_ttl = timedelta(days=cache_days)

    def assess(
        self, taxon: Taxon, lat: float, lon: float, exclude: uuid.UUID | None = None
    ) -> Commonness:
        local_ours = self._verified_nearby(taxon.id, lat, lon, LOCAL_RADIUS_M, exclude)
        if local_ours >= self.min_verified:
            return Commonness.COMMON

        local = self._gbif_count(taxon, "local", LOCAL_STEP_DEG, LOCAL_BOX_DEG, lat, lon)
        if local is None:
            return Commonness.UNCOMMON if local_ours else Commonness.UNKNOWN
        if local >= self.min_gbif:
            return Commonness.COMMON
        if local or local_ours:
            return Commonness.UNCOMMON

        wider = self._gbif_count(taxon, "range", RANGE_STEP_DEG, RANGE_BOX_DEG, lat, lon)
        wider_ours = self._verified_nearby(taxon.id, lat, lon, RANGE_RADIUS_M, exclude)
        if wider == 0 and wider_ours == 0:
            return Commonness.OUT_OF_RANGE
        return Commonness.UNCOMMON

    def _verified_nearby(
        self, taxon_id: int, lat: float, lon: float, radius_m: float, exclude: uuid.UUID | None
    ) -> int:
        point = cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), Geography)
        stmt = select(func.count()).where(
            Observation.taxon_id == taxon_id,
            Observation.status == ObservationStatus.VERIFIED.value,
            func.ST_DWithin(cast(Observation.location, Geography), point, radius_m),
        )
        if exclude is not None:
            stmt = stmt.where(Observation.id != exclude)
        return self.db.scalar(stmt) or 0

    def local_count(self, taxon: Taxon, lat: float, lon: float) -> int | None:
        """Cached GBIF records of the species in its ~55 km cell."""
        return self._gbif_count(taxon, "local", LOCAL_STEP_DEG, LOCAL_BOX_DEG, lat, lon)

    def gbif_key(self, taxon: Taxon) -> int | None:
        return self._gbif_key(taxon)

    def _gbif_count(
        self, taxon: Taxon, scope: str, step: float, box: float, lat: float, lon: float
    ) -> int | None:
        if self.source is None:
            return None
        key = self._gbif_key(taxon)
        if key is None:
            return None
        center_lat, center_lon = cell_center(lat, lon, step)
        cell = f"{scope}:{center_lat:.2f}:{center_lon:.2f}"

        cached = self.db.get(OccurrenceCount, (taxon.id, cell))
        if cached and cached.fetched_at > datetime.now(UTC) - self.cache_ttl:
            return cached.gbif_count

        count = self.source.count_occurrences(
            key,
            max(center_lat - box, -90),
            min(center_lat + box, 90),
            max(center_lon - box, -180),
            min(center_lon + box, 180),
        )
        if count is None:
            return cached.gbif_count if cached else None
        stmt = insert(OccurrenceCount).values(taxon_id=taxon.id, cell=cell, gbif_count=count)
        self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[OccurrenceCount.taxon_id, OccurrenceCount.cell],
                set_={"gbif_count": count, "fetched_at": func.now()},
            )
        )
        return count

    def _gbif_key(self, taxon: Taxon) -> int | None:
        """Seed taxa start without a GBIF key; look it up once and keep it."""
        if taxon.gbif_key is None and self.source is not None:
            match = self.source.match_species(taxon.scientific_name)
            if match is not None:
                clash = self.db.scalar(select(Taxon.id).where(Taxon.gbif_key == match.species_key))
                if clash is None:
                    taxon.gbif_key = match.species_key
        return taxon.gbif_key
