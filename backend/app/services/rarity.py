"""Rarity scores (PRD F2): conservation status, regional frequency and endemism.

score = max(status, 0.75 * frequency + 0.25 * endemism), each component in [0, 1]:
- status: IUCN category (CR 1.0 ... NT 0.3; LC and not-evaluated 0).
- frequency: how often the species is recorded in its ~55 km cell compared with the most
  recorded species of its group there (birds, mammals, reptiles, amphibians or plants), on a
  log scale. Using the group's commonest species as the reference cancels out both how
  heavily an area is sampled and how evenly records spread across a group (plants spread
  far thinner than birds).
- endemism: range size, from the number of countries with GBIF records.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    AreaGroupCount,
    Observation,
    ObservationStatus,
    RarityScore,
    RarityTier,
    Taxon,
)
from app.services.commonness import (
    LOCAL_BOX_DEG,
    LOCAL_RADIUS_M,
    CommonnessService,
    OccurrenceSource,
    cell_center,
    cell_key,
)

STATUS_SCORE = {"EX": 1.0, "EW": 1.0, "CR": 1.0, "EN": 0.8, "VU": 0.6, "NT": 0.3, "DD": 0.2}
# GBIF backbone keys for the comparison groups. Reptilia is split into three classes.
GROUP_KEYS = {
    "Aves": [212],
    "Mammalia": [359],
    "Amphibia": [131],
    "Reptilia": [11592253, 11418114, 11493978],
    "Plantae": [6],
}
REPTILE_CLASSES = {"Reptilia", "Squamata", "Testudines", "Crocodylia"}

# Below this many group records in a cell, relative frequency is just noise.
MIN_GROUP_RECORDS = 50
# Cells with fewer group records than this are under-sampled (explorer bonus, PRD goal 2).
UNDER_SAMPLED_GBIF = 200
UNDER_SAMPLED_OURS = 10

SCORE_TTL = timedelta(days=7)
GROUP_TOTAL_TTL = timedelta(days=30)
RANGE_TTL = timedelta(days=90)

TIERS = [
    (0.8, RarityTier.EXCEPTIONAL),
    (0.6, RarityTier.VERY_RARE),
    (0.4, RarityTier.RARE),
    (0.2, RarityTier.UNCOMMON),
]


def comparison_group(taxon: Taxon) -> str | None:
    if taxon.kingdom == "Plantae":
        return "Plantae"
    if taxon.class_name == "Aves":
        return "Aves"
    if taxon.class_name in ("Mammalia", "Amphibia"):
        return taxon.class_name
    if taxon.class_name in REPTILE_CLASSES:
        return "Reptilia"
    return None


def frequency_score(local: int, top: int, group_total: int) -> float | None:
    """0 when recorded at least a tenth as often as the group's commonest species here;
    1 at a ten-thousandth or less (including never recorded here)."""
    if group_total < MIN_GROUP_RECORDS:
        return None
    relative = (local + 1) / (max(top, local) + 1)
    return _clamp((-math.log10(relative) - 1) / 3)


def endemism_score(countries: int | None) -> float | None:
    if not countries:  # unknown, or too few records anywhere to judge
        return None
    if countries == 1:
        return 1.0
    if countries == 2:
        return 0.75
    if countries <= 4:
        return 0.5
    if countries <= 10:
        return 0.25
    return 0.0


def combine(status: float, frequency: float | None, endemism: float | None) -> float:
    regional = 0.75 * (frequency or 0.0) + 0.25 * (endemism or 0.0)
    return round(max(status, regional), 3)


def tier_for(score: float) -> RarityTier:
    return next((tier for floor, tier in TIERS if score >= floor), RarityTier.COMMON)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


@dataclass(frozen=True)
class Rarity:
    score: float
    tier: RarityTier
    status: float
    frequency: float | None
    endemism: float | None
    local_count: int | None
    group_total: int | None


@dataclass(frozen=True)
class GroupFigures:
    total: int
    top_species: int | None


class RarityService:
    def __init__(
        self, db: Session, commonness: CommonnessService, source: OccurrenceSource | None
    ) -> None:
        self.db = db
        self.commonness = commonness
        self.source = source

    def get(self, taxon: Taxon, lat: float, lon: float) -> Rarity:
        """Cached score for the species in this cell, recomputed when older than a week."""
        cached = self.db.get(RarityScore, (taxon.id, cell_key(lat, lon)))
        if cached and cached.computed_at > datetime.now(UTC) - SCORE_TTL:
            return Rarity(
                cached.score,
                RarityTier(cached.tier),
                cached.status,
                cached.frequency,
                cached.endemism,
                cached.local_count,
                cached.group_total,
            )
        return self.compute(taxon, lat, lon)

    def compute(self, taxon: Taxon, lat: float, lon: float) -> Rarity:
        status = STATUS_SCORE.get(taxon.iucn_status, 0.0)
        gbif_local = self.commonness.local_count(taxon, lat, lon)
        group = self._group(taxon, lat, lon)
        ours_local, ours_group = self._ours(taxon, lat, lon)

        local = group_total = None
        frequency = None
        if group is not None or ours_group:
            local = (gbif_local or 0) + ours_local
            group_total = (group.total if group else 0) + ours_group
            top = (group.top_species or 0) if group else 0
            frequency = frequency_score(local, top, group_total)
        endemism = endemism_score(self._countries(taxon))

        score = combine(status, frequency, endemism)
        rarity = Rarity(score, tier_for(score), status, frequency, endemism, local, group_total)
        self._store(taxon, lat, lon, rarity)
        return rarity

    def is_under_sampled(self, taxon: Taxon, lat: float, lon: float) -> bool:
        group = self._group(taxon, lat, lon)
        if group is None:
            return False  # unknown: don't pay a bonus on missing data
        return group.total < UNDER_SAMPLED_GBIF and self._ours(taxon, lat, lon)[1] < (
            UNDER_SAMPLED_OURS
        )

    # --- Inputs -----------------------------------------------------------------------

    def _ours(self, taxon: Taxon, lat: float, lon: float) -> tuple[int, int]:
        """Our verified records near the point: (this species, its group)."""
        point = cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), Geography)
        near = func.ST_DWithin(cast(Observation.location, Geography), point, LOCAL_RADIUS_M)
        row = self.db.execute(
            select(
                func.count().filter(Observation.taxon_id == taxon.id),
                func.count(),
            )
            .select_from(Observation)
            .join(Taxon, Observation.taxon_id == Taxon.id)
            .where(
                Observation.status == ObservationStatus.VERIFIED.value,
                Taxon.taxon_group == taxon.taxon_group,
                near,
            )
        ).one()
        return row[0] or 0, row[1] or 0

    def _group(self, taxon: Taxon, lat: float, lon: float) -> GroupFigures | None:
        """GBIF totals for the species' group in its cell, cached for a month."""
        group = comparison_group(taxon)
        if group is None or self.source is None:
            return None
        cell = cell_key(lat, lon)
        cached = self.db.get(AreaGroupCount, (cell, group))
        if cached and cached.fetched_at > datetime.now(UTC) - GROUP_TOTAL_TTL:
            return GroupFigures(cached.gbif_count, cached.top_species_count)
        center_lat, center_lon = cell_center(lat, lon)
        box = (
            max(center_lat - LOCAL_BOX_DEG, -90),
            min(center_lat + LOCAL_BOX_DEG, 90),
            max(center_lon - LOCAL_BOX_DEG, -180),
            min(center_lon + LOCAL_BOX_DEG, 180),
        )
        total = self.source.count_occurrences(GROUP_KEYS[group], *box)
        if total is None:
            return GroupFigures(cached.gbif_count, cached.top_species_count) if cached else None
        top = self.source.top_species_count(GROUP_KEYS[group], *box)
        values = {"gbif_count": total, "top_species_count": top}
        stmt = insert(AreaGroupCount).values(cell=cell, group=group, **values)
        self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[AreaGroupCount.cell, AreaGroupCount.group],
                set_={**values, "fetched_at": func.now()},
            )
        )
        return GroupFigures(total, top)

    def _countries(self, taxon: Taxon) -> int | None:
        fresh = taxon.range_checked_at and taxon.range_checked_at > datetime.now(UTC) - RANGE_TTL
        if fresh or self.source is None:
            return taxon.range_country_count
        key = self.commonness.gbif_key(taxon)
        if key is None:
            return taxon.range_country_count
        countries = self.source.country_count(key)
        if countries is not None:
            taxon.range_country_count = countries
            taxon.range_checked_at = datetime.now(UTC)
        return taxon.range_country_count

    def _store(self, taxon: Taxon, lat: float, lon: float, rarity: Rarity) -> None:
        values = {
            "score": rarity.score,
            "tier": rarity.tier.value,
            "status": rarity.status,
            "frequency": rarity.frequency,
            "endemism": rarity.endemism,
            "local_count": rarity.local_count,
            "group_total": rarity.group_total,
        }
        stmt = insert(RarityScore).values(taxon_id=taxon.id, cell=cell_key(lat, lon), **values)
        self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[RarityScore.taxon_id, RarityScore.cell],
                set_={**values, "computed_at": func.now()},
            )
        )
