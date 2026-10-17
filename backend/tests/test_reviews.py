import pytest

from app.adapters.events import subscribe
from app.adapters.species_id import get_species_identifier
from app.main import app
from app.models import Role, User
from tests.helpers import FakeIdentifier, taxon_id, upload

QUEUE = "/api/v1/reviews/queue"
LONDON = ("51.5072", "-0.1276")
KATHMANDU = ("27.7172", "85.3240")


@pytest.fixture(autouse=True)
def low_confidence_ai():
    # Without a commonness baseline and with a weak AI match, everything needs review.
    fake = FakeIdentifier([("Passer domesticus", 0.4)])
    app.dependency_overrides[get_species_identifier] = lambda: fake


@pytest.fixture
def observer(make_user, seeded):
    return make_user()


@pytest.fixture
def experts(make_user):
    return [make_user(Role.VERIFIER) for _ in range(3)]


def submit(client, headers, species="Passer domesticus", where=LONDON):
    lat, lon = where
    return upload(
        client, headers, taxon_id=str(taxon_id(client, species)), latitude=lat, longitude=lon
    ).json()


def review(client, headers, obs_id, decision="confirm", **body):
    return client.post(
        f"/api/v1/reviews/{obs_id}", json={"decision": decision, **body}, headers=headers
    )


def reputation(db, user):
    db.expire_all()
    return db.get(User, user.id).reputation


def test_only_verifiers_and_admins_see_the_queue(client, observer, make_user):
    _, headers = observer
    assert client.get(QUEUE, headers=headers).status_code == 403
    assert client.get(QUEUE, headers=make_user(Role.ANALYST)[1]).status_code == 403
    assert client.get(QUEUE, headers=make_user(Role.ADMIN)[1]).status_code == 200


def test_queue_shows_others_records_rare_first_with_observer_info(client, observer, experts):
    _, headers = observer
    common = submit(client, headers)
    rare = submit(client, headers, "Panthera tigris")
    _, expert = experts[0]

    page = client.get(QUEUE, headers=expert).json()
    assert [item["id"] for item in page["items"]] == [rare["id"], common["id"]]
    assert page["items"][0]["observer"] == {"display_name": "Test User", "reputation": 0}
    assert "low_confidence" in page["items"][1]["verification"]["reasons"]


def test_queue_filters_by_group_and_area(client, observer, experts):
    _, headers = observer
    submit(client, headers, where=LONDON)
    nepal = submit(client, headers, where=KATHMANDU)
    _, expert = experts[0]

    assert client.get(QUEUE, params={"group": "plant"}, headers=expert).json()["total"] == 0
    near = {"latitude": 27.7, "longitude": 85.3, "radius_km": 50}
    items = client.get(QUEUE, params=near, headers=expert).json()["items"]
    assert [i["id"] for i in items] == [nepal["id"]]


def test_saved_review_area_and_groups_apply_by_default(client, observer, experts):
    _, headers = observer
    submit(client, headers, where=LONDON)
    submit(client, headers, where=KATHMANDU)
    _, expert = experts[0]
    prefs = {
        "review_taxon_groups": ["bird"],
        "review_area": {"latitude": 27.7, "longitude": 85.3, "radius_km": 100},
    }
    assert client.patch("/api/v1/auth/me", json=prefs, headers=expert).status_code == 200

    assert client.get(QUEUE, headers=expert).json()["total"] == 1
    assert client.get(QUEUE, params={"anywhere": True}, headers=expert).json()["total"] == 2
    client.patch("/api/v1/auth/me", json={"review_area": None}, headers=expert)
    assert client.get(QUEUE, headers=expert).json()["total"] == 2


def test_observers_cannot_set_review_preferences(client, observer):
    _, headers = observer
    res = client.patch("/api/v1/auth/me", json={"review_taxon_groups": ["bird"]}, headers=headers)
    assert res.status_code == 403


def test_one_expert_confirms_an_ordinary_record(client, db, observer, experts):
    user, headers = observer
    events = []
    subscribe("observation.verified")(lambda e: events.append(e.payload))
    obs = submit(client, headers)
    _, expert = experts[0]

    res = review(client, expert, obs["id"], comment="Clear male, black bib.")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "verified"
    assert body["verification"]["method"] == "expert"
    assert reputation(db, user) == 2
    assert events[0]["observation_id"] == obs["id"]

    mine = client.get(f"/api/v1/observations/{obs['id']}", headers=headers).json()
    assert mine["verification"]["reviews"][0]["comment"] == "Clear male, black bib."
    assert mine["verification"]["reviews"][0]["reviewer_name"] == "Test User"
    # Reviewed items leave the queue.
    assert client.get(QUEUE, headers=experts[1][1]).json()["total"] == 0


def test_rare_record_needs_two_agreeing_experts(client, db, observer, experts):
    user, headers = observer
    obs = submit(client, headers, "Panthera tigris")
    first = review(client, experts[0][1], obs["id"]).json()
    assert first["status"] == "in_review"
    assert first["verification"]["review_count"] == 1

    assert review(client, experts[1][1], obs["id"]).json()["status"] == "verified"
    assert reputation(db, user) == 5


def test_experts_can_correct_the_species(client, db, observer, experts):
    user, headers = observer
    obs = submit(client, headers)
    dove = taxon_id(client, "Columba livia")
    body = review(client, experts[0][1], obs["id"], taxon_id=dove).json()

    assert body["status"] == "verified"
    assert body["taxon"]["id"] == dove
    assert body["observer_taxon"]["scientific_name"] == "Passer domesticus"
    assert reputation(db, user) == 0  # verified, but the observer's ID was wrong


def test_relabelling_as_threatened_requires_a_second_expert(client, observer, experts):
    _, headers = observer
    obs = submit(client, headers)
    tiger = taxon_id(client, "Panthera tigris")
    body = review(client, experts[0][1], obs["id"], taxon_id=tiger).json()
    assert body["status"] == "in_review"
    assert body["verification"]["required_reviews"] == 2
    assert review(client, experts[1][1], obs["id"], taxon_id=tiger).json()["taxon"]["id"] == tiger


def test_disagreeing_experts_need_a_tiebreak(client, observer, experts):
    _, headers = observer
    obs = submit(client, headers, "Panthera tigris")
    tiger = taxon_id(client, "Panthera tigris")
    leopard_ish = taxon_id(client, "Vulpes vulpes")
    review(client, experts[0][1], obs["id"], taxon_id=tiger)
    assert review(client, experts[1][1], obs["id"], taxon_id=leopard_ish).json()["status"] == (
        "in_review"
    )
    final = review(client, experts[2][1], obs["id"], taxon_id=tiger).json()
    assert final["status"] == "verified" and final["taxon"]["id"] == tiger


@pytest.mark.parametrize(("reason", "delta"), [("unidentifiable", -3), ("duplicate_or_stock", -10)])
def test_rejection_needs_a_reason_and_costs_reputation(
    client, db, observer, experts, reason, delta
):
    user, headers = observer
    obs = submit(client, headers)
    _, expert = experts[0]
    assert review(client, expert, obs["id"], "reject").status_code == 422

    body = review(client, expert, obs["id"], "reject", reason=reason).json()
    assert body["status"] == "rejected"
    assert body["verification"]["rejection_reason"] == reason
    assert reputation(db, user) == delta


def test_review_guards(client, make_user, observer, experts):
    expert_user, expert = experts[0]
    _, headers = observer
    obs = submit(client, headers, "Panthera tigris")

    own = submit(client, expert)
    assert review(client, expert, own["id"]).json()["detail"]["code"] == "own_observation"
    assert review(client, expert, obs["id"]).status_code == 200
    assert review(client, expert, obs["id"]).json()["detail"]["code"] == "already_reviewed"
    assert review(client, headers, obs["id"]).status_code == 403  # observers can't review

    needs_id = upload(client, headers).json()
    assert review(client, expert, needs_id["id"]).json()["detail"]["code"] == "not_in_review"
    assert review(client, expert, obs["id"].replace("a", "b").replace("1", "2")).status_code in (
        404,
        409,
    )
