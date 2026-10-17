import pytest
from sqlalchemy import select

from app.adapters.species_id import get_species_identifier
from app.cli import (
    check_seed_statuses,
    refresh_geoprivacy,
    refresh_statuses,
    set_sensitivity,
)
from app.main import app
from app.models import LocationAccess, Observation, Role, StatusSource, Taxon
from app.services import location_access, verification
from tests.helpers import (
    LONDON_SQUARE,
    FakeBaseline,
    FakeIdentifier,
    add_org_with_region,
    taxon_id,
    upload,
)

LONDON = (51.5072, -0.1276)
PUBLIC = "/api/v1/public/observations"
LONDON_BBOX = "-0.6,51.2,0.4,51.8"


@pytest.fixture
def baseline(monkeypatch):
    source = FakeBaseline()
    monkeypatch.setattr(verification, "get_occurrence_source", lambda: source)
    return source


def identify(name, confidence=0.95):
    fake = FakeIdentifier([(name, confidence)])
    app.dependency_overrides[get_species_identifier] = lambda: fake


def submit(client, headers, species, **form):
    return upload(client, headers, taxon_id=str(taxon_id(client, species)), **form).json()


def reveal(client, headers, obs_id, purpose="verification"):
    return client.post(
        f"/api/v1/observations/{obs_id}/exact-location", json={"purpose": purpose}, headers=headers
    )


@pytest.fixture
def tiger_in_review(client, make_user, seeded, baseline):
    identify("Panthera tigris", 0.97)
    owner, headers = make_user()
    obs = submit(client, headers, "Panthera tigris")
    assert obs["status"] == "in_review"
    return obs, headers


def test_observer_always_sees_their_exact_location(client, tiger_in_review):
    obs, headers = tiger_in_review
    assert obs["geoprivacy"] == "obscured"
    assert obs["location_precision"] == "exact"
    assert (obs["latitude"], obs["longitude"]) == LONDON


def test_others_see_threatened_species_only_as_a_grid_cell(client, make_user, tiger_in_review):
    obs, _ = tiger_in_review
    _, verifier = make_user(Role.VERIFIER)
    seen = client.get(f"/api/v1/observations/{obs['id']}", headers=verifier).json()
    assert seen["location_precision"] == "generalised"
    assert (seen["latitude"], seen["longitude"]) == pytest.approx((51.5, -0.1))
    assert seen["accuracy_m"] > 10_000
    queued = client.get("/api/v1/reviews/queue", headers=verifier).json()["items"][0]
    assert queued["location_precision"] == "generalised"


def test_common_species_are_shown_exactly_to_reviewers(client, make_user, seeded, baseline):
    identify("Passer domesticus", 0.4)  # low confidence keeps it in review
    _, headers = make_user()
    obs = submit(client, headers, "Passer domesticus")
    _, verifier = make_user(Role.VERIFIER)
    seen = client.get(f"/api/v1/observations/{obs['id']}", headers=verifier).json()
    assert seen["geoprivacy"] == "open" and seen["location_precision"] == "exact"


def test_observers_can_obscure_their_own_records(client, make_user, seeded, baseline):
    identify("Passer domesticus", 0.4)
    _, headers = make_user()
    obs = submit(client, headers, "Passer domesticus", obscure_location="true")
    assert obs["observer_geoprivacy"] == "obscured" and obs["geoprivacy"] == "obscured"


def test_verifier_reveal_is_audited_and_only_while_in_review(
    client, db, make_user, tiger_in_review
):
    obs, _ = tiger_in_review
    verifier_user, verifier = make_user(Role.VERIFIER)
    res = reveal(client, verifier, obs["id"])
    assert res.status_code == 200
    assert (res.json()["latitude"], res.json()["longitude"]) == LONDON

    entry = db.scalar(select(LocationAccess))
    assert (entry.user_id, entry.purpose, entry.geoprivacy) == (
        verifier_user.id,
        "verification",
        "obscured",
    )
    assert str(entry.observation_id) == obs["id"]

    # Once decided, verifiers lose access; analysts gain it.
    _, second = make_user(Role.VERIFIER)
    client.post(f"/api/v1/reviews/{obs['id']}", json={"decision": "confirm"}, headers=verifier)
    client.post(f"/api/v1/reviews/{obs['id']}", json={"decision": "confirm"}, headers=second)
    assert reveal(client, verifier, obs["id"]).json()["detail"]["code"] == "reveal_not_allowed"
    analyst_user, analyst = make_user(Role.ANALYST)
    # Analysts only see exact locations inside their own organisation's regions.
    assert reveal(client, analyst, obs["id"], "research").status_code == 403
    add_org_with_region(db, analyst_user, LONDON_SQUARE)
    assert reveal(client, analyst, obs["id"], "research").status_code == 200


def test_analysts_cannot_see_unverified_exact_locations(client, make_user, tiger_in_review):
    obs, _ = tiger_in_review
    _, analyst = make_user(Role.ANALYST)
    assert reveal(client, analyst, obs["id"], "research").status_code == 403


def test_owner_reveal_is_not_logged_and_strangers_get_404(client, db, make_user, tiger_in_review):
    obs, headers = tiger_in_review
    assert reveal(client, headers, obs["id"]).status_code == 200
    assert db.scalar(select(LocationAccess)) is None
    _, stranger = make_user()
    assert reveal(client, stranger, obs["id"]).status_code == 404


def test_daily_reveal_limit(client, make_user, tiger_in_review, monkeypatch):
    monkeypatch.setattr(location_access, "DAILY_REVEAL_LIMIT", 2)
    obs, _ = tiger_in_review
    _, verifier = make_user(Role.VERIFIER)
    assert [reveal(client, verifier, obs["id"]).status_code for _ in range(3)] == [200, 200, 429]
    _, admin = make_user(Role.ADMIN)
    assert [reveal(client, admin, obs["id"], "management").status_code for _ in range(3)] == [
        200,
        200,
        200,
    ]


def test_admins_can_read_the_audit_log(client, make_user, tiger_in_review):
    obs, _ = tiger_in_review
    _, verifier = make_user(Role.VERIFIER)
    reveal(client, verifier, obs["id"])
    _, admin = make_user(Role.ADMIN)
    log = client.get(
        "/api/v1/admin/location-access", params={"observation_id": obs["id"]}, headers=admin
    ).json()
    assert [(e["user_role"], e["purpose"]) for e in log] == [("verifier", "verification")]
    assert client.get("/api/v1/admin/location-access", headers=verifier).status_code == 403


def test_public_feed_shows_verified_records_generalised_and_anonymous(
    client, make_user, seeded, baseline
):
    _, headers = make_user()
    identify("Passer domesticus", 0.95)
    submit(client, headers, "Passer domesticus")  # auto-verified, open
    identify("Panthera tigris", 0.97)
    tiger = submit(client, headers, "Panthera tigris", latitude="51.55", longitude="-0.05")
    _, e1 = make_user(Role.VERIFIER)
    _, e2 = make_user(Role.VERIFIER)
    for expert in (e1, e2):
        client.post(f"/api/v1/reviews/{tiger['id']}", json={"decision": "confirm"}, headers=expert)

    feed = client.get(PUBLIC, params={"bbox": LONDON_BBOX})
    assert feed.headers["cache-control"] == "public, max-age=300"
    features = {f["properties"]["scientific_name"]: f for f in feed.json()["features"]}
    assert set(features) == {"Passer domesticus", "Panthera tigris"}
    assert features["Passer domesticus"]["geometry"]["coordinates"] == [-0.128, 51.507]
    assert features["Panthera tigris"]["geometry"]["coordinates"] == pytest.approx([-0.1, 51.5])
    assert features["Panthera tigris"]["properties"]["precision"] == "generalised"
    props = features["Passer domesticus"]["properties"]
    assert not {"user_id", "display_name", "photo_url", "latitude"} & props.keys()
    assert "T" not in props["observed_on"]  # date only


def test_hidden_species_vanish_from_public_feed(client, make_user, seeded, baseline):
    identify("Passer domesticus", 0.95)
    _, headers = make_user()
    obs = submit(client, headers, "Passer domesticus")
    assert len(client.get(PUBLIC, params={"bbox": LONDON_BBOX}).json()["features"]) == 1

    _, admin = make_user(Role.ADMIN)
    res = client.patch(
        f"/api/v1/admin/taxa/{obs['taxon']['id']}", json={"sensitivity": "hidden"}, headers=admin
    )
    assert res.json()["observations_updated"] == 1
    assert client.get(PUBLIC, params={"bbox": LONDON_BBOX}).json()["features"] == []
    _, verifier = make_user(Role.VERIFIER)
    seen = client.get(f"/api/v1/observations/{obs['id']}", headers=verifier).json()
    assert (seen["latitude"], seen["longitude"]) == (51.5, -0.5)  # 1° cell


@pytest.mark.parametrize(
    "bbox", ["nonsense", "0,0,0,0", "-10,0,15,5", "10,10,0,0", "-200,0,-190,5"]
)
def test_public_feed_validates_bbox(client, bbox):
    assert client.get(PUBLIC, params={"bbox": bbox}).status_code == 422


def test_small_search_circles_are_refused(client, make_user):
    _, verifier = make_user(Role.VERIFIER)
    near = {"latitude": 51.5, "longitude": -0.1, "radius_km": 10}
    assert client.get("/api/v1/leaderboards", params=near, headers=verifier).status_code == 422
    assert client.get("/api/v1/reviews/queue", params=near, headers=verifier).status_code == 422
    small_area = {"review_area": {"latitude": 51.5, "longitude": -0.1, "radius_km": 5}}
    assert client.patch("/api/v1/auth/me", json=small_area, headers=verifier).status_code == 422


def _gbif_added_species(db, name="Smutsornis africanus") -> Taxon:
    taxon = Taxon(
        scientific_name=name,
        taxon_group="bird",
        kingdom="Animalia",
        class_name="Aves",
        iucn_status="NE",
        source="gbif",
        gbif_key=424242,
        common_names={"en": "Courser"},
    )
    db.add(taxon)
    db.commit()
    return taxon


def test_unchecked_species_status_is_looked_up_during_routing(
    client, db, make_user, seeded, baseline
):
    taxon = _gbif_added_species(db)
    baseline.iucn = {424242: "EN"}
    identify(taxon.scientific_name, 0.95)
    _, headers = make_user()
    obs = upload(client, headers, taxon_id=str(taxon.id)).json()

    db.expire_all()
    assert db.get(Taxon, taxon.id).iucn_status == "EN"
    assert db.get(Taxon, taxon.id).status_source == StatusSource.GBIF_IUCN
    assert obs["status"] == "in_review" and obs["geoprivacy"] == "obscured"


def test_cli_status_and_sensitivity_tools(client, db, make_user, seeded, baseline):
    taxon = _gbif_added_species(db)
    baseline.iucn = {424242: "LC"}
    assert refresh_statuses() == (1, 1)
    db.expire_all()
    assert db.get(Taxon, taxon.id).status_source == "gbif_iucn"

    mismatches = {name: (seed, iucn) for name, seed, iucn in check_seed_statuses()}
    assert mismatches["Panthera tigris"] == ("EN", "LC")  # the fake says LC for everything
    db.expire_all()
    assert (
        db.scalar(select(Taxon.iucn_status).where(Taxon.scientific_name == "Panthera tigris"))
        == "EN"
    )

    identify("Passer domesticus", 0.95)
    _, headers = make_user()
    submit(client, headers, "Passer domesticus")
    assert set_sensitivity("Passer domesticus", "hidden") == 1
    db.expire_all()
    assert db.scalar(select(Observation.geoprivacy)) == "hidden"
    assert refresh_geoprivacy() == 1


def test_audit_entries_survive_user_deletion(client, db, make_user, tiger_in_review):
    obs, _ = tiger_in_review
    verifier_user, verifier = make_user(Role.VERIFIER)
    email, user_id = verifier_user.email, verifier_user.id
    reveal(client, verifier, obs["id"])
    db.delete(db.get(type(verifier_user), user_id))
    db.commit()
    entry = db.scalar(select(LocationAccess))
    assert (entry.user_id, entry.user_email) == (user_id, email)
