"""GBIF backbone taxonomy lookups (open data, no key; same in local and GCP setups)."""

import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import APP_NAME

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 90


@dataclass(frozen=True)
class GbifMatch:
    species_key: int
    species: str  # accepted name, even when the query was a synonym
    kingdom: str | None
    class_name: str | None
    family: str | None


class GbifClient:
    def __init__(
        self, base_url: str, timeout: float = 5.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": f"{APP_NAME}/0.1"},
        )

    @lru_cache(maxsize=4096)  # noqa: B019  (one long-lived client per process)
    def match_species(self, name: str) -> GbifMatch | None:
        """Exact-ish species match; None when unknown, ambiguous, or GBIF is unreachable."""
        try:
            response = self._http.get("/species/match", params={"name": name, "strict": "true"})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GBIF lookup failed for %r: %s", name, exc)
            return None
        if (
            data.get("matchType") in (None, "NONE", "HIGHERRANK")
            or (data.get("confidence") or 0) < MIN_CONFIDENCE
            or not data.get("speciesKey")
            or not data.get("species")
        ):
            return None
        return GbifMatch(
            species_key=data["speciesKey"],
            species=data["species"],
            kingdom=data.get("kingdom"),
            class_name=data.get("class"),
            family=data.get("family"),
        )

    def count_occurrences(
        self,
        taxon_key: int | list[int],
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
    ) -> int | None:
        """Georeferenced records of a taxon (or any of several) inside a box.

        None if GBIF is unreachable.
        """
        params = {
            "taxonKey": taxon_key if isinstance(taxon_key, list) else [taxon_key],
            "decimalLatitude": f"{lat_min},{lat_max}",
            "decimalLongitude": f"{lon_min},{lon_max}",
            "occurrenceStatus": "PRESENT",
            "hasGeospatialIssue": "false",
            "limit": 0,
        }
        try:
            response = self._http.get("/occurrence/search", params=params)
            response.raise_for_status()
            return int(response.json()["count"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("GBIF occurrence count failed for %s: %s", taxon_key, exc)
            return None

    def top_species_count(
        self, taxon_keys: list[int], lat_min: float, lat_max: float, lon_min: float, lon_max: float
    ) -> int | None:
        """Records of the most-recorded species of a group inside a box (0 if none)."""
        params = {
            "taxonKey": taxon_keys,
            "decimalLatitude": f"{lat_min},{lat_max}",
            "decimalLongitude": f"{lon_min},{lon_max}",
            "occurrenceStatus": "PRESENT",
            "hasGeospatialIssue": "false",
            "facet": "speciesKey",
            "facetLimit": 1,
            "limit": 0,
        }
        try:
            response = self._http.get("/occurrence/search", params=params)
            response.raise_for_status()
            facets = response.json().get("facets") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GBIF species facet failed for %s: %s", taxon_keys, exc)
            return None
        counts = facets[0]["counts"] if facets else []
        return counts[0]["count"] if counts else 0

    def iucn_category(self, species_key: int) -> str | None:
        """IUCN Red List code (e.g. "EN") as mirrored by GBIF; "NE" if not assessed.

        None if GBIF is unreachable. Licence of the IUCN data: see PRD §13, question 2.
        """
        try:
            response = self._http.get(f"/species/{species_key}/iucnRedListCategory")
            if response.status_code == 404:
                return "NE"
            response.raise_for_status()
            return response.json().get("code") or "NE"
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GBIF IUCN lookup failed for %s: %s", species_key, exc)
            return None

    def country_count(self, taxon_key: int, min_records: int = 3) -> int | None:
        """Countries with at least `min_records` records: a range-size (endemism) signal.

        The threshold ignores vagrants and stray mislabelled records.
        """
        params = {
            "taxonKey": taxon_key,
            "occurrenceStatus": "PRESENT",
            "facet": "country",
            "facetLimit": 300,
            "limit": 0,
        }
        try:
            response = self._http.get("/occurrence/search", params=params)
            response.raise_for_status()
            facets = response.json().get("facets") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GBIF country facet failed for %s: %s", taxon_key, exc)
            return None
        counts = facets[0]["counts"] if facets else []
        return sum(1 for c in counts if c.get("count", 0) >= min_records)


@lru_cache
def get_gbif_client(background: bool = False) -> GbifClient:
    from app.config import get_settings

    settings = get_settings()
    timeout = (
        settings.gbif_background_timeout_seconds if background else settings.gbif_timeout_seconds
    )
    return GbifClient(settings.gbif_api_url, timeout=timeout)
