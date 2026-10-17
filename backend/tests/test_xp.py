import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.adapters.events import subscribe
from app.adapters.species_id import get_species_identifier
from app.cli import add_restricted_area, list_restricted_areas, recompute_rarity
from app.main import app
from app.models import RarityScore, Role, XpEntry
from app.services import verification
from tests.helpers import FakeBaseline, FakeIdentifier, taxon_id, unique_photo, upload

PROGRESS = "/api/v1/me/progress"
BOARD = "/api/v1/leaderboards"


@pytest.fixture
def baseline(monkeypatch):
    # Default: species common in its cell, widespread, well-sampled area.
    source = FakeBaseline()
    monkeypatch.setattr(verification, "get_occurrence_source", lambda: source)
    return source


@pytest.fixture
def confident_ai():
    def configure(name="Passer domesticus", confidence=0.95):
        fake = FakeIdentifier([(name, confidence)])
        app.dependency_overrides[get_species_identifier] = lambda: fake

    configure()
    return configure


def submit(client, headers, species="Passer domesticus", **form):
    return upload(client, headers, taxon_id=str(taxon_id(client, species)), **form).json()


def progress(client, headers):
    return client.get(PROGRESS, headers=headers).json()


def test_auto_verified_sighting_confirms_xp_and_first_badge(
    client, make_user, seeded, baseline, confident_ai
):
    _, headers = make_user()
    obs = submit(client, headers)

    assert obs["status"] == "verified"
    rewards = obs["rewards"]
    assert rewards["status"] == "confirmed"
    assert {e["kind"]: e["amount"] for e in rewards["entries"]} == {"base": 10, "first_species": 20}
    assert rewards["rarity_tier"] == "common"

    mine = progress(client, headers)
    assert (mine["xp"], mine["pending_xp"], mine["level"]["number"]) == (30, 0, 1)
    assert [b["code"] for b in mine["badges"]] == ["first_sighting"]
    assert mine["verified"] == 1 and mine["species"] == 1


def test_xp_is_provisional_in_review_and_confirmed_by_experts(
    client, make_user, seeded, baseline, confident_ai
):
    _, headers = make_user()
    _, expert1 = make_user(Role.VERIFIER)
    _, expert2 = make_user(Role.VERIFIER)
    confident_ai("Panthera tigris", 0.97)
    obs = submit(client, headers, "Panthera tigris")

    assert obs["status"] == "in_review"
    assert obs["rewards"]["status"] == "provisional"
    assert obs["rewards"]["rarity_tier"] == "exceptional"
    assert progress(client, headers)["pending_xp"] == 10 + 64 + 20

    for expert in (expert1, expert2):
        client.post(f"/api/v1/reviews/{obs['id']}", json={"decision": "confirm"}, headers=expert)
    mine = progress(client, headers)
    assert (mine["xp"], mine["pending_xp"]) == (94, 0)
    assert {"threatened_witness", "rare_find"} <= {b["code"] for b in mine["badges"]}


def test_rejection_voids_provisional_xp(client, make_user, seeded, baseline, confident_ai):
    _, headers = make_user()
    _, expert = make_user(Role.VERIFIER)
    confident_ai(confidence=0.4)  # low confidence -> review
    obs = submit(client, headers)
    assert progress(client, headers)["pending_xp"] == 30

    client.post(
        f"/api/v1/reviews/{obs['id']}",
        json={"decision": "reject", "reason": "unidentifiable"},
        headers=expert,
    )
    mine = progress(client, headers)
    assert (mine["xp"], mine["pending_xp"]) == (0, 0)
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=headers).json()
    assert detail["rewards"]["xp"] == 0 and detail["rewards"]["entries"] == []


def test_repeat_sighting_same_place_and_day_earns_two(
    client, make_user, seeded, baseline, confident_ai
):
    _, headers = make_user()
    submit(client, headers)
    second = submit(client, headers, latitude="51.5075", longitude="-0.1280")  # ~40 m away
    assert second["rewards"]["xp"] == 2 and second["rewards"]["note"] == "repeat"

    elsewhere = submit(client, headers, latitude="51.60", longitude="-0.1276")  # ~10 km
    assert elsewhere["rewards"]["xp"] == 10 and elsewhere["rewards"]["note"] is None


def test_rare_bonus_daily_cap(client, make_user, seeded, baseline, confident_ai):
    _, headers = make_user()
    _, expert1 = make_user(Role.VERIFIER)
    bonuses = []
    for i, species in enumerate(
        ["Panthera tigris", "Elephas maximus", "Ailurus fulgens", "Manis crassicaudata"]
    ):
        confident_ai(species, 0.97)
        obs = submit(client, headers, species, latitude=str(10 + i), longitude="10")
        bonuses.append(
            next((e["amount"] for e in obs["rewards"]["entries"] if e["kind"] == "rarity"), 0)
        )
    assert bonuses == [64, 64, 64, 8]  # all EN (0.8 -> 64); the day's cap is 200
    assert progress(client, headers)["rarity_bonus_left_today"] == 0


def test_explorer_bonus_in_under_sampled_area(client, make_user, seeded, baseline, confident_ai):
    baseline.group = 80
    _, headers = make_user()
    obs = submit(client, headers)
    kinds = {e["kind"] for e in obs["rewards"]["entries"]}
    assert "explorer" in kinds


def test_restricted_area_earns_nothing_but_record_is_kept(
    client, make_user, seeded, baseline, confident_ai, tmp_path
):
    london = tmp_path / "zone.geojson"
    london.write_text(
        json.dumps(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-0.2, 51.45], [0.0, 51.45], [0.0, 51.55], [-0.2, 51.55], [-0.2, 51.45]]
                    ],
                },
            }
        )
    )
    add_restricted_area("Test nesting colony", london, "Breeding season closure")
    assert [a[1] for a in list_restricted_areas()] == ["Test nesting colony"]

    _, headers = make_user()
    obs = submit(client, headers)
    assert obs["status"] == "verified"
    assert obs["rewards"]["xp"] == 0 and obs["rewards"]["note"] == "restricted_area"


def test_expert_correction_recomputes_for_final_species(
    client, make_user, seeded, baseline, confident_ai
):
    _, headers = make_user()
    _, expert1 = make_user(Role.VERIFIER)
    _, expert2 = make_user(Role.VERIFIER)
    confident_ai("Passer domesticus", 0.4)
    obs = submit(client, headers, "Panthera tigris")  # wrong, rare claim
    assert obs["rewards"]["rarity_tier"] == "exceptional"

    sparrow = taxon_id(client, "Passer domesticus")
    for expert in (expert1, expert2):
        client.post(
            f"/api/v1/reviews/{obs['id']}",
            json={"decision": "confirm", "taxon_id": sparrow},
            headers=expert,
        )
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=headers).json()
    assert detail["taxon"]["id"] == sparrow
    assert detail["rewards"]["rarity_tier"] == "common"
    assert detail["rewards"]["xp"] == 30


def test_deleting_an_observation_removes_its_xp_and_tells_analytics(
    client, db, make_user, seeded, baseline, confident_ai
):
    forgotten = []
    subscribe("observation.deleted")(lambda e: forgotten.append(e.payload["observation_id"]))
    _, headers = make_user()
    obs = submit(client, headers)
    client.delete(f"/api/v1/observations/{obs['id']}", headers=headers)
    assert progress(client, headers)["xp"] == 0
    assert forgotten == [obs["id"]]


def _verified_xp(client, make_user, name, xp_photos=1, where=("51.5072", "-0.1276"), hidden=False):
    user, headers = make_user()
    if hidden:
        client.patch("/api/v1/auth/me", json={"leaderboard_visible": False}, headers=headers)
    lat, lon = where
    for i in range(xp_photos):
        submit(
            client, headers, latitude=str(float(lat) + i * 0.1), longitude=lon, photo=unique_photo()
        )
    return user, headers


def test_leaderboards_rank_visible_users(client, db, make_user, seeded, baseline, confident_ai):
    _, alice = _verified_xp(client, make_user, "alice", xp_photos=3)  # 30 + 10 + 10
    _, bob = _verified_xp(client, make_user, "bob", xp_photos=1)  # 30
    _, kathmandu = _verified_xp(client, make_user, "k", xp_photos=2, where=("27.7172", "85.3240"))
    _verified_xp(client, make_user, "ghost", xp_photos=4, hidden=True)

    board = client.get(BOARD, params={"period": "all"}, headers=bob).json()
    assert [e["xp"] for e in board["entries"]] == [50, 40, 30]
    assert board["me"]["rank"] == 3 and board["me"]["is_me"]
    assert board["scope"] == "global"

    near = client.get(
        BOARD, params={"latitude": 27.7, "longitude": 85.3, "radius_km": 50}, headers=bob
    ).json()
    assert [e["xp"] for e in near["entries"]] == [40]
    assert near["me"] is None and near["scope"] == "nearby"

    # Confirmations older than this week drop off the weekly board.
    db.execute(update(XpEntry).values(confirmed_at=datetime.now(UTC) - timedelta(days=40)))
    db.commit()
    assert client.get(BOARD, params={"period": "week"}, headers=bob).json()["entries"] == []
    assert len(client.get(BOARD, params={"period": "all"}, headers=bob).json()["entries"]) == 3


def test_recompute_rarity_job(client, db, make_user, seeded, baseline, confident_ai):
    _, headers = make_user()
    submit(client, headers)
    db.execute(RarityScore.__table__.delete())
    db.commit()
    assert recompute_rarity() == 1
    assert db.query(RarityScore).count() == 1


def test_profile_shows_leaderboard_visibility(client, make_user):
    _, headers = make_user()
    res = client.patch("/api/v1/auth/me", json={"leaderboard_visible": False}, headers=headers)
    assert res.json()["leaderboard_visible"] is False
    assert progress(client, headers)["leaderboard_visible"] is False
