import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from app.adapters.events import subscribe
from app.adapters.species_id import SpeciesIdError, get_species_identifier
from app.adapters.storage import get_storage
from app.main import app
from app.models import Role
from tests.helpers import OBSERVATIONS as URL
from tests.helpers import upload as _upload
from tests.images import make_jpeg

NOW = datetime.now(UTC)


@pytest.fixture
def observer(make_user, seeded):
    return make_user()


def test_capture_requires_location_consent(client, make_user, seeded):
    _, headers = make_user(consent=False)
    res = _upload(client, headers)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "location_consent_required"


def test_capture_identifies_and_waits_for_species_confirmation(client, observer):
    _, headers = observer
    res = _upload(client, headers)
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "needs_id"
    assert body["location_source"] == "device"
    assert body["habitat"] == "urban_garden"  # suggested by the identifier
    ident = body["identification"]
    assert ident["provider"] == "stub" and ident["error"] is None
    assert [c["rank"] for c in ident["candidates"]] == [1, 2, 3]
    # Stub species all exist in the seed list, so every candidate resolves to a taxon.
    assert all(c["taxon"] and c["in_scope"] for c in ident["candidates"])


def test_photo_url_serves_exif_free_copy(client, observer):
    _, headers = observer
    body = _upload(client, headers, photo=make_jpeg(gps=(1.0, 2.0))).json()
    parts = urlsplit(body["photo_url"])
    res = client.get(f"{parts.path}?{parts.query}")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert b"TestCam" not in res.content


def test_retrying_the_same_capture_is_idempotent(client, observer):
    _, headers = observer
    client_uuid = str(uuid.uuid4())
    first = _upload(client, headers, client_uuid=client_uuid)
    second = _upload(client, headers, client_uuid=client_uuid)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert client.get(f"{URL}/mine", headers=headers).json()["total"] == 1


def test_preselected_species_is_submitted_and_routed(client, observer, db):
    _, headers = observer
    events = []
    subscribe("observation.submitted")(lambda e: events.append(e.payload))
    taxon_id = client.get("/api/v1/taxa", params={"q": "Pavo"}).json()["items"][0]["id"]

    body = _upload(client, headers, taxon_id=str(taxon_id), habitat="forest").json()

    # Routed straight away in-process; without a commonness baseline it goes to review.
    assert body["status"] == "in_review"
    assert body["taxon"]["scientific_name"] == "Pavo cristatus"
    assert body["habitat"] == "forest"  # the observer's choice wins over the suggestion
    assert events == [
        {"observation_id": body["id"], "user_id": events[0]["user_id"], "taxon_id": taxon_id}
    ]


def test_choose_species_after_upload(client, observer):
    _, headers = observer
    body = _upload(client, headers).json()
    chosen = body["identification"]["candidates"][1]["taxon"]["id"]
    res = client.put(f"{URL}/{body['id']}/species", json={"taxon_id": chosen}, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "in_review" and res.json()["taxon"]["id"] == chosen
    assert res.json()["observer_taxon"]["id"] == chosen

    # Once routed to review, only experts can change the identification.
    again = client.put(f"{URL}/{body['id']}/species", json={"taxon_id": chosen}, headers=headers)
    assert again.status_code == 409

    fresh = _upload(client, headers, photo=make_jpeg(color=(10, 20, 30))).json()
    unknown = client.put(f"{URL}/{fresh['id']}/species", json={"taxon_id": 999999}, headers=headers)
    assert unknown.status_code == 422


def test_location_falls_back_to_exif(client, observer):
    _, headers = observer
    res = _upload(
        client, headers, photo=make_jpeg(gps=(-1.2921, 36.8219)), latitude=None, longitude=None
    )
    assert res.status_code == 201
    body = res.json()
    assert body["location_source"] == "exif"
    assert body["latitude"] == pytest.approx(-1.2921, abs=1e-4)


def test_exif_time_without_timezone_uses_device_offset(client, observer):
    _, headers = observer
    photo = make_jpeg(gps=(27.7172, 85.3240), taken_at="2026:10:16 07:45:00")
    res = _upload(
        client,
        headers,
        photo=photo,
        latitude=None,
        longitude=None,
        observed_at=None,
        utc_offset_minutes="345",
    )
    observed = datetime.fromisoformat(res.json()["observed_at"])
    assert observed == datetime(2026, 10, 16, 2, 0, tzinfo=UTC)  # 07:45 at UTC+05:45


@pytest.mark.parametrize(
    ("form", "code"),
    [
        ({"latitude": None, "longitude": None}, "location_required"),
        ({"longitude": None}, "invalid_location"),
        ({"observed_at": (NOW + timedelta(hours=2)).isoformat()}, "observed_in_future"),
        ({"taxon_id": "999999"}, "unknown_taxon"),
    ],
)
def test_rejects_invalid_capture(client, observer, form, code):
    _, headers = observer
    res = _upload(client, headers, **form)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == code


def test_rejects_non_image_and_out_of_range_coordinates(client, observer):
    _, headers = observer
    assert _upload(client, headers, photo=b"not an image").json()["detail"]["code"] == (
        "invalid_photo"
    )
    assert _upload(client, headers, latitude="95").status_code == 422


def test_rejects_oversized_upload(client, observer, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    _, headers = observer
    assert _upload(client, headers).status_code == 413


def test_identification_failure_still_records_capture(client, observer):
    class Broken:
        def identify(self, *args, **kwargs):
            raise SpeciesIdError("model unavailable")

    app.dependency_overrides[get_species_identifier] = Broken
    _, headers = observer
    res = _upload(client, headers)
    assert res.status_code == 201
    ident = res.json()["identification"]
    assert ident["error"] == "model unavailable" and ident["candidates"] == []
    assert res.json()["status"] == "needs_id"


def test_visibility_and_ownership(client, observer, make_user):
    _, owner_headers = observer
    obs_id = _upload(client, owner_headers).json()["id"]
    _, stranger = make_user()
    _, verifier = make_user(Role.VERIFIER)

    assert client.get(f"{URL}/{obs_id}", headers=stranger).status_code == 404
    assert client.get(f"{URL}/{obs_id}", headers=verifier).status_code == 200
    # Reviewers can read but not change someone else's record.
    assert client.delete(f"{URL}/{obs_id}", headers=verifier).status_code == 404
    assert client.get(f"{URL}/mine", headers=stranger).json()["total"] == 0


def test_delete_removes_record_and_photo(client, observer, db):
    user, headers = observer
    body = _upload(client, headers).json()
    key = f"observations/{user.id}/{body['client_uuid']}.jpg"
    assert get_storage().exists(key)

    assert client.delete(f"{URL}/{body['id']}", headers=headers).status_code == 204
    assert client.get(f"{URL}/{body['id']}", headers=headers).status_code == 404
    assert not get_storage().exists(key)


def test_list_mine_filters_by_status(client, observer):
    _, headers = observer
    _upload(client, headers)
    taxon_id = client.get("/api/v1/taxa", params={"q": "Pavo"}).json()["items"][0]["id"]
    _upload(client, headers, photo=make_jpeg(color=(200, 30, 30)), taxon_id=str(taxon_id))
    res = client.get(f"{URL}/mine", params={"status": "in_review"}, headers=headers).json()
    assert res["total"] == 1 and res["items"][0]["status"] == "in_review"
