import httpx

from app.services.gbif import GbifClient


def _client(handler) -> GbifClient:
    return GbifClient("https://api.gbif.test/v1", transport=httpx.MockTransport(handler))


def test_count_occurrences_queries_a_bounding_box():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"count": 16188, "results": []})

    count = _client(handler).count_occurrences(5231190, 27.25, 28.25, 84.8, 85.8)

    assert count == 16188
    assert seen["path"] == "/v1/occurrence/search"
    assert seen["params"] == {
        "taxonKey": "5231190",
        "decimalLatitude": "27.25,28.25",
        "decimalLongitude": "84.8,85.8",
        "occurrenceStatus": "PRESENT",
        "hasGeospatialIssue": "false",
        "limit": "0",
    }


def test_count_occurrences_returns_none_when_gbif_fails():
    assert _client(lambda r: httpx.Response(503)).count_occurrences(1, 0, 1, 0, 1) is None


def test_match_species_returns_accepted_name_for_synonyms():
    payload = {
        "matchType": "EXACT",
        "confidence": 99,
        "speciesKey": 2765942,
        "species": "Pontederia crassipes",
        "kingdom": "Plantae",
        "class": "Liliopsida",
        "family": "Pontederiaceae",
    }
    match = _client(lambda r: httpx.Response(200, json=payload)).match_species(
        "Eichhornia crassipes"
    )
    assert (match.species, match.species_key) == ("Pontederia crassipes", 2765942)


def test_match_species_ignores_weak_or_missing_matches():
    weak = {"matchType": "FUZZY", "confidence": 70, "speciesKey": 1, "species": "X y"}
    assert _client(lambda r: httpx.Response(200, json=weak)).match_species("X yy") is None
    none = {"matchType": "NONE", "confidence": 100}
    assert _client(lambda r: httpx.Response(200, json=none)).match_species("Nope") is None


def test_top_species_count_reads_the_first_facet():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = request.url.params
        return httpx.Response(
            200,
            json={
                "count": 0,
                "facets": [
                    {"field": "SPECIES_KEY", "counts": [{"name": "5229493", "count": 270773}]}
                ],
            },
        )

    assert _client(handler).top_species_count([212], 51.25, 52.25, -0.75, 0.25) == 270773
    assert seen["params"]["facet"] == "speciesKey" and seen["params"]["facetLimit"] == "1"
    empty = {"count": 0, "facets": [{"field": "SPECIES_KEY", "counts": []}]}
    assert (
        _client(lambda r: httpx.Response(200, json=empty)).top_species_count([6], 0, 1, 0, 1) == 0
    )


def test_iucn_category():
    ok = {"category": "ENDANGERED", "code": "EN"}
    assert _client(lambda r: httpx.Response(200, json=ok)).iucn_category(5219416) == "EN"
    assert _client(lambda r: httpx.Response(404)).iucn_category(1) == "NE"
    assert (
        _client(lambda r: httpx.Response(200, json={"category": "NOT_EVALUATED"})).iucn_category(1)
        == "NE"
    )
    assert _client(lambda r: httpx.Response(503)).iucn_category(1) is None
