from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CellCount:
    """Verified records of one species in one grid cell (cell given by its centre)."""

    cell_lat: float
    cell_lon: float
    taxon_id: int
    count: int


class AnalyticsStore(ABC):
    """Analytical queries over verified records (BigQuery on GCP, PRD §9).

    Cells use floor(lat / step), so Postgres, BigQuery and Python agree on boundaries.
    """

    @abstractmethod
    def species_cells(self, step: float) -> list[CellCount]:
        """Every (cell, species) with verified records; drives the rarity recompute job."""

    @abstractmethod
    def record_verified(self, row: dict[str, Any]) -> None:
        """Export a newly verified record (no-op when the operational DB is the source)."""

    @abstractmethod
    def forget(self, observation_id: str) -> None:
        """Remove a deleted observation (account deletion, GDPR-style rights)."""
