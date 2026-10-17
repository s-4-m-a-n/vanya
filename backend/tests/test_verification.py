import io
import random

import pytest
from PIL import Image

from app.adapters.events import subscribe
from app.adapters.species_id import get_species_identifier
from app.main import app
from app.models import Role, User
from app.services import verification
from tests.helpers import FakeBaseline, FakeIdentifier, taxon_id, unique_photo, upload
from tests.images import make_jpeg


@pytest.fixture
def baseline(monkeypatch):
    source = FakeBaseline(local=1000, wide=5000)
    monkeypatch.setattr(verification, "get_occurrence_source", lambda: source)
    return source


@pytest.fixture
def identify():
    def configure(*candidates, photo_flags=(), is_organism=True):
        fake = FakeIdentifier(candidates, photo_flags, is_organism)
        app.dependency_overrides[get_species_identifier] = lambda: fake

    return configure


@pytest.fixture
def events():
    seen = []
    for topic in ("observation.verified", "observation.rejected", "observation.in_review"):
        subscribe(topic)(lambda e: seen.append((e.topic, e.payload)))
    return seen


def submit(client, headers, species: str, photo: bytes | None = None, **form):
    return upload(client, headers, photo=photo, taxon_id=str(taxon_id(client, species)), **form)


def reputation(db, user) -> int:
    db.expire_all()
    return db.get(User, user.id).reputation


def test_confident_common_sighting_is_verified_automatically(
    client, db, make_user, seeded, baseline, identify, events
):
    identify(("Passer domesticus", 0.95))
    user, headers = make_user()
    body = submit(client, headers, "Passer domesticus").json()

    assert body["status"] == "verified"
    assert body["verification"]["method"] == "auto"
    assert body["verification"]["decided_at"]
    assert reputation(db, user) == 1
    assert events[0][0] == "observation.verified"
    assert events[0][1]["taxon_id"] == body["taxon"]["id"]


def test_threatened_species_needs_two_experts(client, make_user, seeded, baseline, identify):
    identify(("Panthera tigris", 0.97))
    _, headers = make_user()
    body = submit(client, headers, "Panthera tigris").json()
    assert body["status"] == "in_review"
    assert body["verification"]["is_rare"] is True
    assert body["verification"]["required_reviews"] == 2


def test_out_of_range_claim_is_rare(client, make_user, seeded, baseline, identify, make_verifier):
    baseline.local = baseline.wide = 0
    identify(("Passer domesticus", 0.95))
    _, headers = make_user()
    obs = submit(client, headers, "Passer domesticus").json()
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=make_verifier()).json()
    assert "out_of_range" in detail["verification"]["reasons"]
    assert detail["verification"]["required_reviews"] == 2


def test_choosing_a_species_the_ai_did_not_suggest_goes_to_review(
    client, make_user, seeded, baseline, identify
):
    identify(("Passer domesticus", 0.95))
    _, headers = make_user()
    assert submit(client, headers, "Columba livia").json()["status"] == "in_review"


def test_same_photo_twice_from_one_person_is_rejected_without_penalty(
    client, db, make_user, seeded, baseline, identify
):
    identify(("Passer domesticus", 0.95))
    user, headers = make_user()
    photo = unique_photo()
    assert submit(client, headers, "Passer domesticus", photo=photo).json()["status"] == "verified"
    second = submit(client, headers, "Passer domesticus", photo=photo).json()

    assert second["status"] == "rejected"
    assert second["verification"]["rejection_reason"] == "duplicate_or_stock"
    assert reputation(db, user) == 1  # only the first upload's +1


def _noise_photo(seed: int, size=(640, 480), quality=90) -> bytes:
    rng = random.Random(seed)
    image = Image.new("RGB", (8, 6))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(48)])
    buffer = io.BytesIO()
    image.resize(size, Image.Resampling.NEAREST).save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("second_photo", "flag"),
    [
        (_noise_photo(7), "duplicate_other"),
        (_noise_photo(7, size=(320, 240), quality=50), "near_duplicate_other"),
    ],
    ids=["exact", "resized"],
)
def test_someone_elses_photo_is_flagged_for_review(
    client, make_user, seeded, baseline, identify, make_verifier, second_photo, flag
):
    identify(("Passer domesticus", 0.95))
    _, first = make_user()
    _, second = make_user()
    submit(client, first, "Passer domesticus", photo=_noise_photo(7))
    obs = submit(client, second, "Passer domesticus", photo=second_photo).json()

    assert obs["status"] == "in_review"
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=make_verifier()).json()
    assert flag in detail["verification"]["reasons"]


def test_photo_gps_far_from_device_location_is_flagged(
    client, make_user, seeded, baseline, identify, make_verifier
):
    identify(("Passer domesticus", 0.95))
    _, headers = make_user()
    photo = make_jpeg(color=(1, 2, 3), gps=(-33.86, 151.21))  # Sydney; device says London
    obs = submit(client, headers, "Passer domesticus", photo=photo).json()
    flags = client.get(f"/api/v1/observations/{obs['id']}", headers=make_verifier()).json()[
        "verification"
    ]["integrity_flags"]
    assert {"code": "location_mismatch", "severity": "hard"}.items() <= flags[0].items()


def test_captive_animal_flag_from_identifier_forces_review(
    client, make_user, seeded, baseline, identify
):
    identify(("Panthera tigris", 0.99), photo_flags=["captive_or_cultivated"])
    _, headers = make_user()
    assert submit(client, headers, "Panthera tigris").json()["status"] == "in_review"


def test_observers_do_not_see_check_details_but_reviewers_do(
    client, make_user, seeded, baseline, identify, make_verifier
):
    identify(("Passer domesticus", 0.4))
    _, headers = make_user()
    obs = submit(client, headers, "Passer domesticus").json()
    own = client.get(f"/api/v1/observations/{obs['id']}", headers=headers).json()["verification"]
    staff = client.get(f"/api/v1/observations/{obs['id']}", headers=make_verifier()).json()
    assert own["reasons"] == [] and own["integrity_flags"] == []
    assert staff["verification"]["reasons"] == ["low_confidence"]


@pytest.fixture
def make_verifier(make_user):
    def factory() -> dict:
        return make_user(Role.VERIFIER)[1]

    return factory
