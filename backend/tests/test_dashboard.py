from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Role, Taxon, WatchlistEntry
from tests.helpers import LONDON_SQUARE, add_org_with_region, make_verified, square

NOW = datetime.now(UTC)
PARIS = (48.8566, 2.3522)
LONDON = (51.5072, -0.1276)


def taxon(db, name) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == name))


@pytest.fixture
def setup(db, client, make_user, seeded):
    analyst_user, analyst = make_user(Role.ANALYST)
    observer, _ = make_user()
    org = add_org_with_region(db, analyst_user, LONDON_SQUARE)
    sparrow, dove = taxon(db, "Passer domesticus"), taxon(db, "Columba livia")
    tiger, toad = taxon(db, "Panthera tigris"), taxon(db, "Rhinella marina")
    db.add(WatchlistEntry(organisation_id=org.id, taxon_id=toad.id, category="invasive"))
    db.commit()

    def add(t, where=LONDON, days_ago=1, **fields):
        return make_verified(db, observer, t, *where, NOW - timedelta(days=days_ago), **fields)

    for d in (1, 2, 3, 40, 41, 45):
        add(sparrow, days_ago=d)  # 3 recent, 3 in the previous 30 days
    add(dove, days_ago=200)
    add(dove, days_ago=5, rarity_tier="rare")
    add(tiger, days_ago=2)
    add(toad, days_ago=3)
    add(sparrow, where=PARIS)  # outside the organisation's region
    return {
        "org": org,
        "headers": analyst,
        "observer": observer,
        "sparrow": sparrow,
        "tiger": tiger,
    }


def get(client, setup, panel, **params):
    res = client.get(
        f"/api/v1/orgs/{setup['org'].id}/dashboard/{panel}", params=params, headers=setup["headers"]
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_summary_counts_only_records_in_the_organisations_regions(client, setup):
    summary = get(client, setup, "summary")
    assert summary["observations"] == 10  # Paris excluded
    assert summary["species"] == 4
    assert (summary["rare_finds"], summary["threatened"], summary["invasive"]) == (1, 1, 1)
    assert summary["top_species"][0]["taxon"]["scientific_name"] == "Passer domesticus"
    assert summary["top_species"][0]["count"] == 6


def test_filters(client, setup):
    start = (NOW - timedelta(days=10)).date().isoformat()
    assert get(client, setup, "summary", start=start)["observations"] == 6
    assert get(client, setup, "summary", group="plant")["observations"] == 0
    only_tiger = get(client, setup, "summary", taxon_id=setup["tiger"].id)
    assert only_tiger["observations"] == 1


def test_map_points_use_generalised_locations(client, setup):
    features = get(client, setup, "observations")["features"]
    assert len(features) == 10
    by_name = {f["properties"]["taxon"]["scientific_name"]: f for f in features}
    assert by_name["Panthera tigris"]["geometry"]["coordinates"] == pytest.approx([-0.1, 51.5])
    assert by_name["Panthera tigris"]["properties"]["geoprivacy"] == "obscured"
    assert by_name["Passer domesticus"]["geometry"]["coordinates"] == [-0.128, 51.507]
    assert by_name["Passer domesticus"]["properties"]["photo_url"].startswith("/media/")


def test_grid_aggregates(client, setup):
    cells = get(client, setup, "grid", step=0.5)["features"]
    assert sum(c["properties"]["observations"] for c in cells) == 10
    assert (
        client.get(
            f"/api/v1/orgs/{setup['org'].id}/dashboard/grid",
            params={"step": 0.3},
            headers=setup["headers"],
        ).status_code
        == 422
    )


def test_trends_by_month(client, setup):
    months = get(client, setup, "trends")
    assert sum(m["observations"] for m in months) == 10
    assert months == sorted(months, key=lambda m: m["month"])
    assert all(set(m["by_group"]) <= {"bird", "animal", "plant"} for m in months)


def test_changes_compare_with_previous_period(client, setup):
    start = (NOW - timedelta(days=30)).date().isoformat()
    end = NOW.date().isoformat()
    result = get(
        client,
        setup,
        "changes",
        start=start,
        end=end,
    )
    # Sparrows: 3 now vs 3 in the previous 30 days -> unchanged, not listed.
    assert all(i["taxon"]["scientific_name"] != "Passer domesticus" for i in result["increases"])
    assert result["decreases"] == []


def test_rare_invasive_and_new_record_panels(client, setup):
    rare = {r["taxon"]["scientific_name"] for r in get(client, setup, "rare")}
    assert rare == {"Panthera tigris", "Columba livia"}
    assert [r["taxon"]["scientific_name"] for r in get(client, setup, "invasive")] == [
        "Rhinella marina"
    ]
    recent = (NOW - timedelta(days=10)).date().isoformat()
    new = {r["taxon"]["scientific_name"] for r in get(client, setup, "new-records", start=recent)}
    # The dove was first recorded 200 days ago, so it isn't new.
    assert new == {"Panthera tigris", "Rhinella marina"}


def test_tiny_regions_cannot_pinpoint_protected_records(db, client, make_user, seeded):
    analyst_user, analyst = make_user(Role.ANALYST)
    observer, _ = make_user()
    # A 400 m square around the tiger's exact spot, away from its 0.2° cell centre.
    org = add_org_with_region(db, analyst_user, square(-0.1276, 51.5072, 0.002), name="Snoop")
    make_verified(db, observer, taxon(db, "Panthera tigris"), *LONDON, NOW)
    make_verified(db, observer, taxon(db, "Passer domesticus"), *LONDON, NOW)
    res = client.get(f"/api/v1/orgs/{org.id}/dashboard/observations", headers=analyst).json()
    names = [f["properties"]["taxon"]["scientific_name"] for f in res["features"]]
    assert names == ["Passer domesticus"]


def test_dashboard_is_for_members_only(client, make_user, setup):
    _, stranger = make_user(Role.ANALYST)
    url = f"/api/v1/orgs/{setup['org'].id}/dashboard/summary"
    assert client.get(url, headers=stranger).status_code == 404
    _, admin = make_user(Role.ADMIN)
    assert client.get(url, headers=admin).status_code == 200


def test_unknown_region_is_404(client, setup):
    res = client.get(
        f"/api/v1/orgs/{setup['org'].id}/dashboard/summary",
        params={"region_id": 999999},
        headers=setup["headers"],
    )
    assert res.status_code == 404


def test_analyst_reveal_is_limited_to_their_regions(db, client, setup):
    paris_tiger = make_verified(db, setup["observer"], setup["tiger"], *PARIS, NOW)
    london = get(client, setup, "rare")
    london_tiger = next(r for r in london if r["taxon"]["scientific_name"] == "Panthera tigris")

    def reveal(obs_id):
        return client.post(
            f"/api/v1/observations/{obs_id}/exact-location",
            json={"purpose": "research"},
            headers=setup["headers"],
        ).status_code

    assert reveal(london_tiger["id"]) == 200
    assert reveal(paris_tiger.id) == 403


def test_habitat_iucn_rarity_and_verification_filters(db, client, setup):
    make_verified(db, setup["observer"], setup["sparrow"], *LONDON, NOW, habitat="wetland")
    make_verified(
        db,
        setup["observer"],
        setup["sparrow"],
        *LONDON,
        NOW,
        habitat="forest",
        verification_method="expert",
    )
    count = lambda **p: get(client, setup, "summary", **p)["observations"]  # noqa: E731
    assert count(habitat=["wetland"]) == 1
    assert count(habitat=["wetland", "forest"]) == 2
    assert count(iucn=["EN"]) == 1  # the tiger
    assert count(rarity=["rare"]) == 1  # the dove
    assert count(verification="expert") == 1
    assert count(verification="auto") == 11


def test_watchlist_and_protection_filters(client, setup):
    invasive = get(client, setup, "summary", watchlist="invasive")
    assert invasive["observations"] == 1
    assert invasive["top_species"][0]["taxon"]["scientific_name"] == "Rhinella marina"
    protected = get(client, setup, "observations", protected=True)["features"]
    assert {f["properties"]["taxon"]["scientific_name"] for f in protected} == {"Panthera tigris"}
    assert get(client, setup, "summary", protected=False)["observations"] == 9


def test_invalid_filter_values_are_rejected(client, setup):
    res = client.get(
        f"/api/v1/orgs/{setup['org'].id}/dashboard/summary",
        params={"habitat": "moon"},
        headers=setup["headers"],
    )
    assert res.status_code == 422


def test_csv_export_follows_filters(client, setup):
    res = client.get(
        f"/api/v1/orgs/{setup['org'].id}/dashboard/export.csv",
        params={"group": "bird"},
        headers=setup["headers"],
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    lines = res.text.strip().splitlines()
    assert lines[0].startswith("record_id,observed_at,scientific_name")
    assert len(lines) == 1 + 8  # 6 sparrows + 2 doves in the region; Paris excluded
    assert all(",bird," in line for line in lines[1:])
