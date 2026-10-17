import pytest

from app.models import Role
from tests.helpers import LONDON_SQUARE, taxon_id

ORGS = "/api/v1/orgs"


@pytest.fixture
def admin(make_user):
    return make_user(Role.ADMIN)


@pytest.fixture
def org(client, admin):
    _, headers = admin
    res = client.post(ORGS, json={"name": "Thames Wildlife Trust", "kind": "ngo"}, headers=headers)
    assert res.status_code == 201
    return res.json()


def test_only_platform_admins_create_organisations(client, make_user):
    _, analyst = make_user(Role.ANALYST)
    assert client.post(ORGS, json={"name": "X", "kind": "ngo"}, headers=analyst).status_code == 403


def test_creator_is_org_admin_and_names_are_unique(client, admin, org):
    _, headers = admin
    assert org["my_role"] == "admin"
    dup = client.post(ORGS, json={"name": "Thames Wildlife Trust", "kind": "park"}, headers=headers)
    assert dup.status_code == 409


def test_members_see_their_organisations_and_strangers_get_404(client, make_user, admin, org):
    _, admin_headers = admin
    member_user, member = make_user()
    _, stranger = make_user(Role.ANALYST)
    res = client.post(
        f"{ORGS}/{org['id']}/members", json={"email": member_user.email}, headers=admin_headers
    )
    assert res.json()["role"] == "analyst"
    assert [o["name"] for o in client.get(ORGS, headers=member).json()] == ["Thames Wildlife Trust"]
    assert client.get(ORGS, headers=stranger).json() == []
    assert client.get(f"{ORGS}/{org['id']}/regions", headers=stranger).status_code == 404
    # Joining makes an observer an analyst (for audited exact locations in the org's regions).
    assert client.get("/api/v1/auth/me", headers=member).json()["role"] == "analyst"


def test_only_org_admins_manage_members_and_regions(client, make_user, admin, org):
    _, admin_headers = admin
    analyst_user, analyst = make_user()
    client.post(
        f"{ORGS}/{org['id']}/members", json={"email": analyst_user.email}, headers=admin_headers
    )
    region = {"name": "Patch", "geometry": LONDON_SQUARE}
    assert (
        client.post(f"{ORGS}/{org['id']}/regions", json=region, headers=analyst).status_code == 403
    )
    other_user, _ = make_user()
    body = {"email": other_user.email}
    assert client.post(f"{ORGS}/{org['id']}/members", json=body, headers=analyst).status_code == 403


def test_last_admin_cannot_be_removed(client, admin, org):
    admin_user, headers = admin
    res = client.delete(f"{ORGS}/{org['id']}/members/{admin_user.id}", headers=headers)
    assert res.json()["detail"]["code"] == "last_admin"


def test_regions_are_validated_repaired_and_measured(client, admin, org):
    _, headers = admin
    url = f"{ORGS}/{org['id']}/regions"
    made = client.post(url, json={"name": "London", "geometry": LONDON_SQUARE}, headers=headers)
    assert made.status_code == 201
    assert made.json()["geometry"]["type"] == "MultiPolygon"
    assert 3000 < made.json()["area_km2"] < 5000  # 0.8° x 0.8° at 51.5°N

    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    fixed = client.post(url, json={"name": "Bowtie", "geometry": bowtie}, headers=headers)
    assert fixed.status_code == 201 and fixed.json()["area_km2"] > 0  # self-intersection repaired

    for bad in (
        {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1]]]},  # not closed
        {"type": "Polygon", "coordinates": [[[0, 0], [200, 0], [1, 1], [0, 0]]]},
    ):
        res = client.post(url, json={"name": "Bad", "geometry": bad}, headers=headers)
        assert res.status_code == 422 and res.json()["detail"]["code"] == "invalid_geometry"

    listed = client.get(url, headers=headers).json()
    assert [r["name"] for r in listed] == ["Bowtie", "London"]
    assert client.delete(f"{url}/{listed[0]['id']}", headers=headers).status_code == 204


def test_watchlist(client, admin, org, seeded):
    _, headers = admin
    toad = taxon_id(client, "Rhinella marina")
    url = f"{ORGS}/{org['id']}/watchlist"
    res = client.put(
        f"{url}/{toad}", json={"category": "invasive", "note": "Spreading"}, headers=headers
    )
    assert res.json()["taxon"]["scientific_name"] == "Rhinella marina"
    client.put(f"{url}/{toad}", json={"category": "priority"}, headers=headers)
    assert [(e["category"], e["note"]) for e in client.get(url, headers=headers).json()] == [
        ("priority", None)
    ]
    assert (
        client.put(f"{url}/999999", json={"category": "invasive"}, headers=headers).status_code
        == 422
    )
    client.delete(f"{url}/{toad}", headers=headers)
    assert client.get(url, headers=headers).json() == []
