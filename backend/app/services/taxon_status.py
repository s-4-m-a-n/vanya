"""Conservation status lookups: the IUCN Red List category via GBIF.

Species added automatically from GBIF start as "not evaluated"; until their category is
looked up they're treated as sensitive. Curated (seed) and admin-set statuses are never
overwritten automatically.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IucnStatus, Observation, StatusSource, Taxon
from app.services.commonness import CommonnessService, OccurrenceSource
from app.services.geoprivacy import apply_geoprivacy

VALID = {s.value for s in IucnStatus}


class TaxonStatusService:
    def __init__(
        self, db: Session, commonness: CommonnessService, source: OccurrenceSource | None
    ) -> None:
        self.db = db
        self.commonness = commonness
        self.source = source

    def refresh(self, taxon: Taxon, force: bool = False) -> bool:
        """Look up the IUCN category if unchecked (or forced). Returns True if checked."""
        if self.source is None:
            return False
        if (
            taxon.status_source in (StatusSource.SEED.value, StatusSource.MANUAL.value)
            and not force
        ):
            return False
        if taxon.status_source == StatusSource.GBIF_IUCN.value and not force:
            return True
        key = self.commonness.gbif_key(taxon)
        code = self.source.iucn_category(key) if key is not None else None
        if code not in VALID:
            return False  # GBIF unreachable: stays unchecked, so still treated as sensitive
        taxon.iucn_status = code
        taxon.status_source = StatusSource.GBIF_IUCN.value
        taxon.status_checked_at = datetime.now(UTC)
        return True

    def lookup(self, taxon: Taxon) -> str | None:
        """The GBIF/IUCN category without changing anything (for auditing seed data)."""
        if self.source is None:
            return None
        key = self.commonness.gbif_key(taxon)
        return self.source.iucn_category(key) if key is not None else None


def refresh_taxon_observations(db: Session, taxon: Taxon) -> int:
    """Re-apply location protection after a species' status or sensitivity changes."""
    observations = db.scalars(select(Observation).where(Observation.taxon_id == taxon.id)).all()
    for obs in observations:
        apply_geoprivacy(obs, taxon)
    return len(observations)
