"""Sensitive-location protection (PRD F5): how precisely a record may be shown.

Only the observer, and roles that explicitly request it through the audited
exact-location endpoint, ever see true coordinates. Everything else (public feeds,
leaderboards, review-queue area filters, reviewer views of sensitive records) uses the
generalised point computed here.
"""

import math
from dataclasses import dataclass

from app.models import Geoprivacy, Observation, Taxon

# Threatened (or extinct-in-the-wild) species are obscured by default; Data Deficient
# species are too, since being unassessed often hides real threat.
SENSITIVE_STATUSES = {"CR", "EN", "VU", "EW", "EX", "DD"}
OBSCURED_STEP_DEG = 0.2  # ~22 km cells
HIDDEN_STEP_DEG = 1.0  # ~110 km cells, for coarse internal filters only
OPEN_DECIMALS = 3  # ~110 m: protects observers' homes without hurting the data
OPEN_MIN_ACCURACY_M = 110
METRES_PER_DEGREE = 111_320
_ORDER = [Geoprivacy.OPEN, Geoprivacy.OBSCURED, Geoprivacy.HIDDEN]


def taxon_geoprivacy(taxon: Taxon | None) -> Geoprivacy:
    if taxon is None:
        return Geoprivacy.OBSCURED  # species not known yet: be careful
    if taxon.sensitivity:
        return Geoprivacy(taxon.sensitivity)
    if taxon.iucn_status in SENSITIVE_STATUSES:
        return Geoprivacy.OBSCURED
    if taxon.status_source is None:
        return Geoprivacy.OBSCURED  # status never checked: could be threatened
    return Geoprivacy.OPEN


def strictest(*levels: Geoprivacy) -> Geoprivacy:
    return max(levels, key=_ORDER.index)


@dataclass(frozen=True)
class GeneralisedPoint:
    latitude: float
    longitude: float
    accuracy_m: float


def generalise(
    lat: float, lon: float, accuracy_m: float | None, level: Geoprivacy
) -> GeneralisedPoint:
    if level == Geoprivacy.OPEN:
        return GeneralisedPoint(
            round(lat, OPEN_DECIMALS),
            round(lon, OPEN_DECIMALS),
            max(accuracy_m or 0.0, OPEN_MIN_ACCURACY_M),
        )
    step = OBSCURED_STEP_DEG if level == Geoprivacy.OBSCURED else HIDDEN_STEP_DEG
    center_lat = (math.floor(lat / step) + 0.5) * step
    center_lon = (math.floor(lon / step) + 0.5) * step
    # Half the cell's diagonal: the point is somewhere within this distance.
    half_lat = step / 2 * METRES_PER_DEGREE
    half_lon = half_lat * math.cos(math.radians(center_lat))
    return GeneralisedPoint(
        round(center_lat, 6), round(center_lon, 6), round(math.hypot(half_lat, half_lon))
    )


def apply_geoprivacy(obs: Observation, taxon: Taxon | None) -> None:
    """Set the effective level and generalised point. Pass the taxon explicitly: the
    relationship may not be loaded yet when the species was just chosen."""
    level = strictest(Geoprivacy(obs.observer_geoprivacy or "open"), taxon_geoprivacy(taxon))
    point = generalise(obs.latitude, obs.longitude, obs.accuracy_m, level)
    obs.geoprivacy = level.value
    obs.generalised_latitude = point.latitude
    obs.generalised_longitude = point.longitude
    obs.generalised_accuracy_m = point.accuracy_m
