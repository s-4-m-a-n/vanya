"""Organisation dashboard queries (PRD F4): verified records inside an organisation's
regions of interest, by species, region and time.

Region membership and every coordinate returned use the generalised point, so a small
region can't be used to pinpoint a protected record (exact locations stay behind the
audited endpoint). Runs on Postgres for the MVP; the aggregations are plain SQL
(floor grids, date_trunc, counts) and port to BigQuery.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from app.adapters.storage import BlobStorage
from app.models import (
    Observation,
    ObservationStatus,
    RegionOfInterest,
    Taxon,
    WatchCategory,
    WatchlistEntry,
)

RARE_TIERS = ("rare", "very_rare", "exceptional")
THREATENED = ("CR", "EN", "VU", "EW", "EX")
GRID_STEPS = (0.2, 0.5, 1.0)


@dataclass(frozen=True)
class Scope:
    org_id: uuid.UUID
    region_id: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    group: str | None = None
    taxon_id: int | None = None


class DashboardService:
    def __init__(self, db: Session, storage: BlobStorage, locale: str = "en") -> None:
        self.db = db
        self.storage = storage
        self.locale = locale

    # --- Filters -------------------------------------------------------------------------

    def _in_regions(self, scope: Scope):  # noqa: ANN202
        stmt = select(RegionOfInterest.id).where(
            RegionOfInterest.organisation_id == scope.org_id,
            func.ST_Intersects(RegionOfInterest.area, Observation.generalised_location),
        )
        if scope.region_id is not None:
            stmt = stmt.where(RegionOfInterest.id == scope.region_id)
        return stmt.exists()

    def _conditions(self, scope: Scope, with_time: bool = True) -> list:
        conditions = [
            Observation.status == ObservationStatus.VERIFIED.value,
            self._in_regions(scope),
        ]
        if with_time and scope.start is not None:
            conditions.append(Observation.observed_at >= scope.start)
        if with_time and scope.end is not None:
            conditions.append(Observation.observed_at < scope.end)
        if scope.group:
            conditions.append(Taxon.taxon_group == scope.group)
        if scope.taxon_id is not None:
            conditions.append(Observation.taxon_id == scope.taxon_id)
        return conditions

    def _watched(self, scope: Scope, category: WatchCategory):  # noqa: ANN202
        return select(WatchlistEntry.taxon_id).where(
            WatchlistEntry.organisation_id == scope.org_id,
            WatchlistEntry.category == category.value,
        )

    def _from(self, *columns):  # noqa: ANN001, ANN202
        return (
            select(*columns).select_from(Observation).join(Taxon, Observation.taxon_id == Taxon.id)
        )

    # --- Panels --------------------------------------------------------------------------

    def summary(self, scope: Scope) -> dict[str, Any]:
        invasive = self._watched(scope, WatchCategory.INVASIVE)
        row = self.db.execute(
            self._from(
                func.count(Observation.id),
                func.count(distinct(Observation.taxon_id)),
                func.count(distinct(Observation.user_id)),
                func.count(Observation.id).filter(Observation.rarity_tier.in_(RARE_TIERS)),
                func.count(Observation.id).filter(Taxon.iucn_status.in_(THREATENED)),
                func.count(Observation.id).filter(Observation.taxon_id.in_(invasive)),
            ).where(*self._conditions(scope))
        ).one()
        top = self.db.execute(
            self._from(Taxon, func.count(Observation.id).label("n"))
            .where(*self._conditions(scope))
            .group_by(Taxon.id)
            .order_by(func.count(Observation.id).desc(), Taxon.scientific_name)
            .limit(10)
        ).all()
        return {
            "observations": row[0],
            "species": row[1],
            "observers": row[2],
            "rare_finds": row[3],
            "threatened": row[4],
            "invasive": row[5],
            "top_species": [{"taxon": self._taxon(t), "count": n} for t, n in top],
        }

    def points(self, scope: Scope, limit: int = 2000) -> dict[str, Any]:
        rows = self.db.execute(
            self._from(Observation, Taxon)
            .where(*self._conditions(scope))
            .order_by(Observation.observed_at.desc())
            .limit(limit)
        ).all()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [obs.generalised_longitude, obs.generalised_latitude],
                    },
                    "properties": self._record(obs, taxon),
                }
                for obs, taxon in rows
            ],
        }

    def grid(self, scope: Scope, step: float) -> dict[str, Any]:
        """Counts per square grid cell, for areas with too many points to draw."""
        lat_cell = func.floor(Observation.generalised_latitude / step)
        lon_cell = func.floor(Observation.generalised_longitude / step)
        rows = self.db.execute(
            self._from(
                lat_cell,
                lon_cell,
                func.count(Observation.id),
                func.count(distinct(Observation.taxon_id)),
            )
            .where(*self._conditions(scope))
            .group_by(lat_cell, lon_cell)
        ).all()
        features = []
        for lat, lon, count, species in rows:
            south, west = lat * step, lon * step
            ring = [
                [west, south],
                [west + step, south],
                [west + step, south + step],
                [west, south + step],
                [west, south],
            ]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"observations": count, "species": species},
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def trends(self, scope: Scope) -> list[dict[str, Any]]:
        month = func.date_trunc("month", Observation.observed_at)
        rows = self.db.execute(
            self._from(
                month,
                Taxon.taxon_group,
                func.count(Observation.id),
                func.count(distinct(Observation.taxon_id)),
            )
            .where(*self._conditions(scope))
            .group_by(month, Taxon.taxon_group)
            .order_by(month)
        ).all()
        series: dict[str, dict[str, Any]] = {}
        for period, group, count, species in rows:
            key = period.date().isoformat()[:7]
            entry = series.setdefault(
                key, {"month": key, "observations": 0, "by_group": {}, "species": 0}
            )
            entry["observations"] += count
            entry["by_group"][group] = count
            entry["species"] += species  # per-group distinct counts; groups don't overlap
        return list(series.values())

    def changes(self, scope: Scope, min_count: int = 3, top: int = 5) -> dict[str, Any]:
        """Species recorded more or less often than in the previous period of equal length.

        Raw counts reflect observer effort as well as the species, so treat these as
        leads to investigate rather than population trends.
        """
        end = scope.end or datetime.now(tz=scope.start.tzinfo if scope.start else None)
        start = scope.start or end - timedelta(days=90)
        previous = replace(scope, start=start - (end - start), end=start)
        current = replace(scope, start=start, end=end)

        def counts(window: Scope) -> dict[int, int]:
            rows = self.db.execute(
                self._from(Observation.taxon_id, func.count(Observation.id))
                .where(*self._conditions(window))
                .group_by(Observation.taxon_id)
            ).all()
            return dict(rows)

        now_counts, before = counts(current), counts(previous)
        changes = []
        for taxon_id in now_counts.keys() | before.keys():
            a, b = before.get(taxon_id, 0), now_counts.get(taxon_id, 0)
            if max(a, b) < min_count:
                continue
            changes.append((taxon_id, a, b))
        taxa = {
            t.id: t
            for t in self.db.scalars(select(Taxon).where(Taxon.id.in_([c[0] for c in changes])))
        }

        def item(taxon_id: int, a: int, b: int) -> dict[str, Any]:
            return {
                "taxon": self._taxon(taxa[taxon_id]),
                "previous": a,
                "current": b,
                "change_pct": round((b - a) / a * 100) if a else None,
            }

        ordered = sorted(changes, key=lambda c: c[2] - c[1])
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "increases": [item(*c) for c in reversed(ordered) if c[2] > c[1]][:top],
            "decreases": [item(*c) for c in ordered if c[2] < c[1]][:top],
        }

    def rare(self, scope: Scope, limit: int = 50) -> list[dict[str, Any]]:
        return self._records(
            scope,
            [
                (Observation.rarity_tier.in_(RARE_TIERS)) | (Taxon.iucn_status.in_(THREATENED)),
            ],
            limit,
        )

    def invasive(self, scope: Scope, limit: int = 50) -> list[dict[str, Any]]:
        return self._records(
            scope, [Observation.taxon_id.in_(self._watched(scope, WatchCategory.INVASIVE))], limit
        )

    def new_records(self, scope: Scope, limit: int = 50) -> list[dict[str, Any]]:
        """Species first verified in the organisation's regions within the period."""
        first = (
            self._from(
                Observation.taxon_id,
                func.min(Observation.observed_at).label("first_seen"),
                func.count(Observation.id).label("records"),
            )
            .where(*self._conditions(scope, with_time=False))
            .group_by(Observation.taxon_id)
            .subquery()
        )
        stmt = select(Taxon, first.c.first_seen, first.c.records).join(
            first, first.c.taxon_id == Taxon.id
        )
        if scope.start is not None:
            stmt = stmt.where(first.c.first_seen >= scope.start)
        if scope.end is not None:
            stmt = stmt.where(first.c.first_seen < scope.end)
        rows = self.db.execute(stmt.order_by(first.c.first_seen.desc()).limit(limit)).all()
        return [
            {"taxon": self._taxon(t), "first_seen": seen.isoformat(), "records": n}
            for t, seen, n in rows
        ]

    # --- Helpers -------------------------------------------------------------------------

    def _records(self, scope: Scope, extra: list, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            self._from(Observation, Taxon)
            .where(and_(*self._conditions(scope), *extra))
            .order_by(Observation.observed_at.desc())
            .limit(limit)
        ).all()
        return [self._record(obs, taxon) for obs, taxon in rows]

    def _record(self, obs: Observation, taxon: Taxon) -> dict[str, Any]:
        return {
            "id": str(obs.id),
            "taxon": self._taxon(taxon),
            "observed_at": obs.observed_at.isoformat(),
            "latitude": obs.generalised_latitude,
            "longitude": obs.generalised_longitude,
            "accuracy_m": obs.generalised_accuracy_m,
            "geoprivacy": obs.geoprivacy,
            "rarity_tier": obs.rarity_tier,
            "habitat": obs.habitat,
            "photo_url": self.storage.signed_url(obs.photo_key),
        }

    def _taxon(self, taxon: Taxon) -> dict[str, Any]:
        return {
            "id": taxon.id,
            "scientific_name": taxon.scientific_name,
            "common_name": taxon.common_name(self.locale),
            "taxon_group": taxon.taxon_group,
            "iucn_status": taxon.iucn_status,
        }
