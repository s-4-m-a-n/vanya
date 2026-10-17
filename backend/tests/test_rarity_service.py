import pytest
from sqlalchemy import select

from app.models import AreaGroupCount, RarityScore, Taxon
from app.services.commonness import CommonnessService, cell_center, cell_key
from app.services.rarity import RarityService, comparison_group
from tests.helpers import FakeBaseline

KATHMANDU = (27.7172, 85.3240)


def service(db, source):
    return RarityService(db, CommonnessService(db, source, 20, 5, 30), source)


def taxon(db, name) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == name))


def test_common_species_scores_low_and_is_cached(db, seeded):
    sparrow = taxon(db, "Passer domesticus")
    rarity = service(db, FakeBaseline(local=16_000, group=690_000, top=24_000, countries=200)).get(
        sparrow, *KATHMANDU
    )
    db.commit()
    assert rarity.tier == "common" and rarity.score == 0.0
    stored = db.get(RarityScore, (sparrow.id, cell_key(*KATHMANDU)))
    assert stored.local_count == 16_000 and stored.group_total == 690_000
    cached = db.scalar(select(AreaGroupCount))
    assert (cached.gbif_count, cached.top_species_count) == (690_000, 24_000)
    assert sparrow.range_country_count == 200


def test_threatened_status_dominates(db, seeded):
    rarity = service(db, FakeBaseline()).get(taxon(db, "Panthera tigris"), *KATHMANDU)
    assert rarity.status == 0.8 and rarity.tier == "exceptional"


def test_scarce_range_restricted_species_is_rare(db, seeded):
    rarity = service(db, FakeBaseline(local=30, group=500_000, top=30_000, countries=1)).get(
        taxon(db, "Passer domesticus"), *KATHMANDU
    )
    assert rarity.frequency == pytest.approx(0.66, abs=0.01)  # 1/1000 of the commonest bird
    assert rarity.endemism == 1.0  # one country
    assert rarity.score == pytest.approx(0.745, abs=0.005)
    assert rarity.tier == "very_rare"


def test_without_gbif_rarity_falls_back_to_status_only(db, seeded):
    rarity = service(db, None).get(taxon(db, "Antigone antigone"), *KATHMANDU)  # VU
    assert (rarity.score, rarity.frequency, rarity.endemism) == (0.6, None, None)


def test_under_sampled_cells(db, seeded):
    sparrow = taxon(db, "Passer domesticus")
    assert service(db, FakeBaseline(group=120)).is_under_sampled(sparrow, *KATHMANDU)
    assert not service(db, FakeBaseline(group=5000)).is_under_sampled(sparrow, 10.0, 10.0)
    assert not service(db, None).is_under_sampled(sparrow, *KATHMANDU)


def test_comparison_groups(db, seeded):
    assert comparison_group(taxon(db, "Passer domesticus")) == "Aves"
    assert comparison_group(taxon(db, "Panthera tigris")) == "Mammalia"
    assert comparison_group(taxon(db, "Python bivittatus")) == "Reptilia"
    assert comparison_group(taxon(db, "Quercus robur")) == "Plantae"


@pytest.mark.parametrize("lat", [27.5, 27.4999999, 27.75, -0.25, -0.2500001])
def test_cells_use_floor_so_sql_and_python_agree(lat):
    center, _ = cell_center(lat, 0.0)
    assert center - 0.25 <= lat < center + 0.25
