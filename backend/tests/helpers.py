"""Shared helpers for API-level tests."""

import itertools
import uuid
from datetime import UTC, datetime, timedelta

from app.adapters.species_id import Candidate, IdResult
from app.services.gbif import GbifMatch
from tests.images import make_jpeg

OBSERVATIONS = "/api/v1/observations"
_colours = itertools.count(1)


def unique_photo(**kwargs) -> bytes:
    """A photo no other test upload shares, so duplicate checks stay quiet."""
    n = next(_colours)
    return make_jpeg(color=(n * 37 % 256, n * 91 % 256, n * 53 % 256), **kwargs)


def upload_form(**overrides) -> dict:
    form = {
        "client_uuid": str(uuid.uuid4()),
        "latitude": "51.5072",
        "longitude": "-0.1276",
        "accuracy_m": "12",
        "observed_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
    }
    form.update(overrides)
    return {k: v for k, v in form.items() if v is not None}


def upload(client, headers, photo: bytes | None = None, **form):
    files = {"photo": ("photo.jpg", photo or unique_photo(), "image/jpeg")}
    return client.post(OBSERVATIONS, data=upload_form(**form), files=files, headers=headers)


def taxon_id(client, query: str) -> int:
    return client.get("/api/v1/taxa", params={"q": query}).json()["items"][0]["id"]


class FakeIdentifier:
    """Identifier returning fixed candidates: [(scientific_name, confidence), ...]."""

    def __init__(self, candidates=(), photo_flags=(), is_organism=True):
        self.result = IdResult(
            provider="fake",
            is_organism=is_organism,
            candidates=[Candidate(n, None, "bird", c) for n, c in candidates],
            photo_flags=list(photo_flags),
        )

    def identify(self, *args, **kwargs) -> IdResult:
        return self.result


class FakeBaseline:
    """GBIF stand-in.

    `local`/`wide`: species records in the ±0.5° / ±2.5° boxes; `group`: records of the
    species' whole group (e.g. all birds) in the cell; `countries`: range size.
    """

    def __init__(
        self,
        local: int | None = 1000,
        wide: int | None = 5000,
        group: int | None = 50_000,
        top: int | None = 5_000,
        countries: int | None = 50,
        iucn: dict[str, str] | None = None,
    ):
        self.local, self.wide, self.group, self.countries = local, wide, group, countries
        self.top = top
        # GBIF key -> IUCN code; unknown keys are "LC".
        self.iucn = iucn or {}
        self.count_calls = 0

    def match_species(self, name: str) -> GbifMatch | None:
        return GbifMatch(abs(hash(name)) % 10_000_000, name, "Animalia", "Aves", None)

    def count_occurrences(self, key, lat_min, lat_max, lon_min, lon_max) -> int | None:
        if isinstance(key, list):  # a whole group, e.g. all birds
            return self.group
        self.count_calls += 1
        return self.local if lat_max - lat_min <= 1.5 else self.wide

    def top_species_count(self, keys, lat_min, lat_max, lon_min, lon_max) -> int | None:
        return self.top

    def country_count(self, key, min_records=3) -> int | None:
        return self.countries

    def iucn_category(self, key) -> str | None:
        return self.iucn.get(key, "LC")


def square(lon: float, lat: float, half: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


LONDON_SQUARE = square(-0.12, 51.5, 0.4)


def add_org_with_region(db, member, geometry, name="Test Trust", role="analyst"):
    """An organisation with one region and `member` in it; returns the organisation."""
    from app.models import MemberRole
    from app.services.organisations import OrganisationService

    service = OrganisationService(db)
    org = service.create(name, "ngo")
    service.add_region(org, "Patch", geometry)
    service.add_member(org, member.email, MemberRole(role))
    return org


def make_verified(db, user, taxon, lat, lon, observed_at, **fields):
    """Insert a verified observation directly (fast setup for dashboard tests)."""
    import hashlib

    from app.models import Observation
    from app.services.geoprivacy import apply_geoprivacy

    obs = Observation(
        user_id=user.id,
        client_uuid=uuid.uuid4(),
        status="verified",
        taxon_id=taxon.id,
        observer_taxon_id=taxon.id,
        latitude=lat,
        longitude=lon,
        location_source="device",
        observed_at=observed_at,
        photo_key=f"observations/{uuid.uuid4()}.jpg",
        photo_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        photo_width=1,
        photo_height=1,
        verification_method="auto",
        **fields,
    )
    apply_geoprivacy(obs, taxon)
    db.add(obs)
    db.commit()
    return obs
