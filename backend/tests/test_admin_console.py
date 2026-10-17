from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Role, Taxon
from tests.helpers import LONDON_SQUARE, add_org_with_region, make_verified, square

NOW = datetime.now(UTC)


def taxon(db, name) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == name))


def test_console_is_admin_only(client, make_user):
    _, verifier = make_user(Role.VERIFIER)
    for path in ("overview", "users", "organisations", "taxa", "restricted-areas"):
        assert client.get(f"/api/v1/admin/{path}", headers=verifier).status_code == 403


def test_overview_counts(db, client, make_user, seeded):
    _, admin = make_user(Role.ADMIN)
    observer, _ = make_user()
    sparrow = taxon(db, "Passer domesticus")
    make_verified(db, observer, sparrow, 51.5, -0.12, NOW, decided_at=NOW)
    make_verified(db, observer, sparrow, 51.5, -0.12, NOW - timedelta(days=60))
    body = client.get("/api/v1/admin/overview", headers=admin).json()
    assert body["users"] == 2
    assert body["users_by_role"] == {"admin": 1, "observer": 1}
    assert body["observations_by_status"]["verified"] == 2
    assert body["verified_7d"] == 1
    assert len(body["daily"]) == 30
    assert body["daily"][-1]["verified"] == 1
    assert body["top_observers"][0]["verified"] == 2


def test_user_search_and_role_filter(db, client, make_user, seeded):
    _, admin = make_user(Role.ADMIN, email="boss@example.org")
    observer, _ = make_user(email="asha@example.org")
    make_verified(db, observer, taxon(db, "Passer domesticus"), 51.5, -0.12, NOW)
    found = client.get("/api/v1/admin/users", params={"q": "ASHA"}, headers=admin).json()
    assert found["total"] == 1
    assert found["items"][0]["verified"] == 1
    admins = client.get("/api/v1/admin/users", params={"role": "admin"}, headers=admin).json()
    assert [u["email"] for u in admins["items"]] == ["boss@example.org"]


def test_organisations_list(db, client, make_user):
    admin_user, admin = make_user(Role.ADMIN)
    add_org_with_region(db, admin_user, LONDON_SQUARE, name="Thames Trust", role="admin")
    orgs = client.get("/api/v1/admin/organisations", headers=admin).json()
    assert orgs[0]["name"] == "Thames Trust"
    assert (orgs[0]["members"], orgs[0]["regions"]) == (1, 1)
    assert orgs[0]["admins"] == [admin_user.email]


def test_taxa_search_and_protected_filter(db, client, make_user, seeded):
    _, admin = make_user(Role.ADMIN)
    tigers = client.get("/api/v1/admin/taxa", params={"q": "tiger"}, headers=admin).json()
    assert tigers[0]["taxon"]["scientific_name"] == "Panthera tigris"
    protected = client.get(
        "/api/v1/admin/taxa", params={"protected_only": True}, headers=admin
    ).json()
    names = {t["taxon"]["scientific_name"] for t in protected}
    assert "Panthera tigris" in names
    assert "Passer domesticus" not in names


def test_restricted_areas_lifecycle(client, make_user):
    _, admin = make_user(Role.ADMIN)
    res = client.post(
        "/api/v1/admin/restricted-areas",
        json={"name": "Heronry", "reason": "Breeding colony", "geojson": square(-0.1, 51.5, 0.01)},
        headers=admin,
    )
    assert res.status_code == 201, res.text
    area = res.json()
    assert area["active"] and area["area_km2"] > 1
    off = client.patch(
        f"/api/v1/admin/restricted-areas/{area['id']}", json={"active": False}, headers=admin
    )
    assert off.json()["active"] is False
    bad = client.post(
        "/api/v1/admin/restricted-areas",
        json={"name": "Bad", "geojson": {"type": "Point", "coordinates": [0, 0]}},
        headers=admin,
    )
    assert bad.status_code == 422
