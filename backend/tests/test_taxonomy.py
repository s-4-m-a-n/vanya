import pytest

from app.models import Taxon, TaxonSource
from app.services.gbif import GbifMatch
from app.services.taxonomy import TaxonResolver


class FakeGbif:
    def __init__(self, matches):
        self.matches = matches

    def match_species(self, name):
        return self.matches.get(name)


@pytest.fixture
def resolver(db, seeded):
    gbif = FakeGbif(
        {
            "Eichhornia crassipes": GbifMatch(
                2765942, "Pontederia crassipes", "Plantae", "Liliopsida", "Pontederiaceae"
            ),
            "Felis catus": GbifMatch(2435035, "Felis catus", "Animalia", "Mammalia", "Felidae"),
            "Apis mellifera": GbifMatch(1341976, "Apis mellifera", "Animalia", "Insecta", "Apidae"),
        }
    )
    return TaxonResolver(db, gbif)


def test_existing_taxon_matches_case_insensitively(resolver):
    result = resolver.resolve("passer domesticus", "House Sparrow", "en", "bird")
    assert result.taxon.scientific_name == "Passer domesticus" and result.in_scope


def test_synonym_maps_to_existing_accepted_taxon_and_records_gbif_key(resolver, db):
    result = resolver.resolve("Eichhornia crassipes", "Jacinto de agua", "es", "plant")
    assert result.taxon.scientific_name == "Pontederia crassipes"
    assert result.taxon.gbif_key == 2765942
    assert result.taxon.common_names["es"] == "Jacinto de agua"


def test_new_species_from_another_locale_also_stores_english_name(resolver, db):
    result = resolver.resolve("Felis catus", "बिल्ली", "hi", "animal", english_name="Cat")
    assert result.taxon.common_names == {"en": "Cat", "hi": "बिल्ली"}


def test_new_in_scope_species_is_added_from_gbif(resolver, db):
    result = resolver.resolve("Felis catus", "Domestic Cat", "en", "animal")
    db.commit()
    taxon = db.get(Taxon, result.taxon.id)
    assert (taxon.taxon_group, taxon.source, taxon.iucn_status) == (
        "animal",
        TaxonSource.GBIF,
        "NE",
    )


def test_out_of_scope_class_is_not_added(resolver):
    result = resolver.resolve("Apis mellifera", "Honey Bee", "en", "animal")
    assert result.taxon is None and result.in_scope is False


def test_unknown_name_is_unresolved_but_keeps_identifier_scope(resolver):
    assert resolver.resolve("Madeupus fakeus", None, "en", "bird").in_scope is True
    assert resolver.resolve("Madeupus fakeus", None, "en", "other").in_scope is False


def test_without_gbif_only_local_matches(db, seeded):
    resolver = TaxonResolver(db, gbif=None)
    assert resolver.resolve("Felis catus", "Cat", "en", "animal").taxon is None
    assert resolver.resolve("Pavo cristatus", None, "en", "bird").taxon is not None
