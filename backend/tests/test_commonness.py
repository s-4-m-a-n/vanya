import uuid

import pytest
from sqlalchemy import select

from app.models import Observation, OccurrenceCount, Taxon
from app.services.commonness import Commonness, CommonnessService
from tests.helpers import FakeBaseline

KATHMANDU = (27.7172, 85.3240)


@pytest.fixture
def sparrow(db, seeded) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == "Passer domesticus"))


def service(db, source, min_gbif=20, min_verified=5):
    return CommonnessService(db, source, min_gbif, min_verified, cache_days=30)


@pytest.mark.parametrize(
    ("local", "wide", "expected"),
    [
        (500, 5000, Commonness.COMMON),
        (3, 5000, Commonness.UNCOMMON),
        (0, 40, Commonness.UNCOMMON),
        (0, 0, Commonness.OUT_OF_RANGE),
        (None, None, Commonness.UNKNOWN),
    ],
)
def test_assess_from_gbif_counts(db, sparrow, local, wide, expected):
    assert service(db, FakeBaseline(local, wide)).assess(sparrow, *KATHMANDU) == expected


def test_without_a_baseline_commonness_is_unknown(db, sparrow):
    assert service(db, None).assess(sparrow, *KATHMANDU) == Commonness.UNKNOWN


def test_our_own_verified_records_count_as_evidence(db, sparrow, make_user):
    user, _ = make_user()
    for i in range(5):
        db.add(
            Observation(
                user_id=user.id,
                client_uuid=uuid.uuid4(),
                status="verified",
                taxon_id=sparrow.id,
                latitude=KATHMANDU[0] + i * 0.01,
                longitude=KATHMANDU[1],
                location_source="device",
                observed_at="2026-10-01T00:00:00Z",
                photo_key=f"k{i}",
                photo_sha256=f"{i:064}",
                photo_width=1,
                photo_height=1,
            )
        )
    db.commit()
    assert service(db, None).assess(sparrow, *KATHMANDU) == Commonness.COMMON


def test_seed_taxon_gets_a_gbif_key_and_counts_are_cached(db, sparrow):
    source = FakeBaseline(500, 5000)
    svc = service(db, source)
    svc.assess(sparrow, *KATHMANDU)
    svc.assess(sparrow, KATHMANDU[0] + 0.02, KATHMANDU[1] + 0.02)  # same 0.5° cell
    db.commit()

    assert sparrow.gbif_key is not None
    assert source.count_calls == 1
    assert db.scalar(select(OccurrenceCount.gbif_count)) == 500


def test_unreachable_gbif_falls_back_to_cached_count(db, sparrow):
    service(db, FakeBaseline(500, 5000)).assess(sparrow, *KATHMANDU)
    db.execute(OccurrenceCount.__table__.update().values(fetched_at="2000-01-01T00:00:00Z"))
    assert service(db, FakeBaseline(None, None)).assess(sparrow, *KATHMANDU) == Commonness.COMMON
