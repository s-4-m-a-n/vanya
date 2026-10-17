import pytest
from sqlalchemy import select, update

from app.adapters.species_id import get_species_identifier
from app.cli import route_pending, set_password, set_role, simulate_reviews
from app.main import app
from app.models import Observation, Role, User
from tests.helpers import FakeIdentifier, taxon_id, upload
from tests.images import make_jpeg


def test_set_role(make_user, db):
    user, _ = make_user()
    set_role(user.email, Role.VERIFIER)
    db.expire_all()
    assert db.get(User, user.id).role == "verifier"


def test_set_password_lets_the_user_sign_in(client, make_user):
    user, _ = make_user()
    set_password(user.email.upper(), "a-new-password")
    res = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "a-new-password"}
    )
    assert res.status_code == 200


def test_set_password_rejects_short_passwords(make_user):
    user, _ = make_user()
    with pytest.raises(SystemExit):
        set_password(user.email, "short")


def test_simulated_experts_confirm_clean_records_and_reject_flagged_ones(
    client, db, make_user, seeded
):
    app.dependency_overrides[get_species_identifier] = lambda: FakeIdentifier(
        [("Passer domesticus", 0.4)]
    )
    _, headers = make_user()
    sparrow = str(taxon_id(client, "Passer domesticus"))
    clean = upload(client, headers, taxon_id=sparrow).json()
    # Photo GPS in Sydney while the device reports London.
    moved = upload(
        client, headers, photo=make_jpeg(color=(9, 9, 9), gps=(-33.86, 151.21)), taxon_id=sparrow
    ).json()

    results = dict(simulate_reviews(limit=10))

    assert results == {clean["id"]: "verified", moved["id"]: "rejected"}
    db.expire_all()
    experts = db.scalars(select(User).where(User.email.like("sim-expert-%"))).all()
    assert {e.display_name for e in experts} == {
        "Simulated expert 1 (demo)",
        "Simulated expert 2 (demo)",
    }
    assert db.get(Observation, moved["id"]).rejection_reason == "wrong_location_or_time"


def test_route_pending_routes_stuck_submissions(client, db, make_user, seeded):
    _, headers = make_user()
    obs = upload(client, headers, taxon_id=str(taxon_id(client, "Pavo"))).json()
    db.execute(update(Observation).values(status="submitted"))
    db.commit()

    assert route_pending() == 1
    db.expire_all()
    assert db.get(Observation, obs["id"]).status == "in_review"


def test_simulated_experts_follow_a_real_experts_correction(client, db, make_user, seeded):
    app.dependency_overrides[get_species_identifier] = lambda: FakeIdentifier(
        [("Passer domesticus", 0.4)]
    )
    _, observer = make_user()
    _, expert = make_user(Role.VERIFIER)
    obs = upload(client, observer, taxon_id=str(taxon_id(client, "Panthera tigris"))).json()
    myna = taxon_id(client, "Acridotheres tristis")
    client.post(
        f"/api/v1/reviews/{obs['id']}",
        json={"decision": "confirm", "taxon_id": myna},
        headers=expert,
    )

    assert dict(simulate_reviews(limit=10)) == {obs["id"]: "verified"}
    db.expire_all()
    verified = db.get(Observation, obs["id"])
    assert verified.taxon_id == myna
    assert verified.is_rare is False  # the tiger claim was rare; a common myna isn't
