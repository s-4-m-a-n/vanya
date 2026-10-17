from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.adapters.events import subscribe
from app.adapters.species_id import get_species_identifier
from app.main import app
from app.models import Alert, AlertDelivery, Role, Taxon, WatchlistEntry
from app.services import alerts as alerts_module
from app.services import verification, webhooks
from app.services.webhooks import DeliveryResult
from tests.helpers import (
    LONDON_SQUARE,
    FakeBaseline,
    FakeIdentifier,
    add_org_with_region,
    make_verified,
    taxon_id,
    upload,
)

NOW = datetime.now(UTC)
LONDON = (51.5072, -0.1276)
PARIS = (48.8566, 2.3522)


def taxon(db, name) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == name))


@pytest.fixture
def org(db, make_user, seeded):
    member, headers = make_user(Role.ANALYST)
    organisation = add_org_with_region(db, member, LONDON_SQUARE, role="admin")
    db.add(
        WatchlistEntry(
            organisation_id=organisation.id,
            taxon_id=taxon(db, "Sciurus carolinensis").id,
            category="invasive",
        )
    )
    db.add(
        WatchlistEntry(
            organisation_id=organisation.id,
            taxon_id=taxon(db, "Vulpes vulpes").id,
            category="priority",
        )
    )
    db.commit()
    return organisation, headers


def evaluate(db, obs):
    from app.adapters.events import get_event_bus
    from app.config import get_settings

    return alerts_module.AlertService(db, get_event_bus(), get_settings()).evaluate(obs.id)


def kinds(alerts):
    return sorted(a.kind for a in alerts)


def test_invasive_sighting_in_region_raises_invasive_and_new_record_alerts(db, make_user, org):
    observer, _ = make_user()
    obs = make_verified(db, observer, taxon(db, "Sciurus carolinensis"), *LONDON, NOW)
    assert kinds(evaluate(db, obs)) == ["invasive", "new_record"]


def test_second_record_is_not_new_and_redelivery_does_not_duplicate(db, make_user, org):
    observer, _ = make_user()
    first = make_verified(db, observer, taxon(db, "Passer domesticus"), *LONDON, NOW)
    assert kinds(evaluate(db, first)) == ["new_record"]
    assert evaluate(db, first) == []  # same event delivered twice
    second = make_verified(db, observer, taxon(db, "Passer domesticus"), *LONDON, NOW)
    assert evaluate(db, second) == []


def test_priority_species_and_settings_toggles(client, db, make_user, org):
    organisation, headers = org
    observer, _ = make_user()
    fox = make_verified(db, observer, taxon(db, "Vulpes vulpes"), *LONDON, NOW)
    assert kinds(evaluate(db, fox)) == ["new_record", "priority"]

    client.put(
        f"/api/v1/orgs/{organisation.id}/alert-settings",
        json={"notify_invasive": False, "notify_new_record": False},
        headers=headers,
    )
    squirrel = make_verified(db, observer, taxon(db, "Sciurus carolinensis"), *LONDON, NOW)
    assert evaluate(db, squirrel) == []


def test_records_outside_regions_raise_nothing(db, make_user, org):
    observer, _ = make_user()
    obs = make_verified(db, observer, taxon(db, "Sciurus carolinensis"), *PARIS, NOW)
    assert evaluate(db, obs) == []


def test_alert_payload_carries_only_the_generalised_location(db, make_user, org):
    observer, _ = make_user()
    tiger = make_verified(db, observer, taxon(db, "Panthera tigris"), *LONDON, NOW)
    (alert,) = evaluate(db, tiger)
    location = alert.payload["location"]
    assert (location["latitude"], location["longitude"]) == pytest.approx((51.5, -0.1))
    assert location["precision"] == "generalised"
    assert str(LONDON[0]) not in str(alert.payload)  # exact coordinates never leave
    assert alert.payload["region"]["name"] == "Patch"


def test_verification_raises_alerts_end_to_end(client, db, make_user, org, monkeypatch):
    organisation, headers = org
    monkeypatch.setattr(verification, "get_occurrence_source", lambda: FakeBaseline())
    app.dependency_overrides[get_species_identifier] = lambda: FakeIdentifier(
        [("Sciurus carolinensis", 0.95)]
    )
    created = []
    subscribe("alert.created")(lambda e: created.append(e.payload["alert_id"]))
    _, observer = make_user()
    obs = upload(client, observer, taxon_id=str(taxon_id(client, "Sciurus carolinensis"))).json()
    assert obs["status"] == "verified"

    inbox = client.get(f"/api/v1/orgs/{organisation.id}/alerts", headers=headers).json()
    assert inbox["unread"] == 2 and len(created) == 2
    invasive = next(a for a in inbox["items"] if a["kind"] == "invasive")
    assert invasive["payload"]["taxon"]["scientific_name"] == "Sciurus carolinensis"

    res = client.post(
        f"/api/v1/orgs/{organisation.id}/alerts/{invasive['id']}/acknowledge", headers=headers
    )
    assert res.json()["acknowledged_at"] is not None
    unread = client.get(
        f"/api/v1/orgs/{organisation.id}/alerts", params={"unread_only": True}, headers=headers
    ).json()
    assert unread["unread"] == 1 and len(unread["items"]) == 1
    _, stranger = make_user(Role.ANALYST)
    assert client.get(f"/api/v1/orgs/{organisation.id}/alerts", headers=stranger).status_code == 404


def test_webhook_settings_delivery_and_secret(client, db, make_user, org, monkeypatch):
    organisation, headers = org
    monkeypatch.setattr(webhooks, "_resolve", lambda host, port: ["93.184.215.14"])
    url = f"/api/v1/orgs/{organisation.id}/alert-settings"

    saved = client.put(
        url, json={"webhook_url": "https://hooks.example.org/dh"}, headers=headers
    ).json()
    assert saved["webhook_url"] == "https://hooks.example.org/dh" and saved["has_webhook_secret"]
    assert "webhook_secret" not in saved
    secret = client.post(f"{url}/rotate-secret", headers=headers).json()["webhook_secret"]
    assert secret.startswith("whsec_")

    unsafe = client.put(url, json={"webhook_url": "http://hooks.example.org/x"}, headers=headers)
    assert unsafe.json()["detail"]["code"] == "unsafe_webhook"

    sent = []

    def fake_send(target, key, payload, allow_local=False, transport=None):
        sent.append((target, key, payload))
        return DeliveryResult(True, 200)

    monkeypatch.setattr(alerts_module, "send", fake_send)
    assert client.post(f"{url}/test-webhook", headers=headers).json()["ok"] is True
    observer, _ = make_user()
    obs = make_verified(db, observer, taxon(db, "Sciurus carolinensis"), *LONDON, NOW)
    evaluate(db, obs)  # each new alert publishes alert.created; the handler delivers it
    assert [p["type"] for _, _, p in sent] == ["test", "alert", "alert"]
    assert all(key == secret for _, key, _ in sent)
    assert "latitude" in sent[1][2]["location"]
    assert db.query(AlertDelivery).count() == 3


def test_only_org_admins_change_alert_settings(client, db, make_user, org):
    organisation, _ = org
    analyst_user, analyst = make_user(Role.ANALYST)
    from app.models import MemberRole
    from app.services.organisations import OrganisationService

    OrganisationService(db).add_member(organisation, analyst_user.email, MemberRole.ANALYST)
    url = f"/api/v1/orgs/{organisation.id}/alert-settings"
    assert client.get(url, headers=analyst).status_code == 200
    assert client.put(url, json={"notify_priority": False}, headers=analyst).status_code == 403


def test_deleting_the_observation_removes_its_alerts(client, db, make_user, org):
    observer, observer_headers = make_user()
    obs = make_verified(
        db, observer, taxon(db, "Sciurus carolinensis"), *LONDON, NOW - timedelta(days=1)
    )
    evaluate(db, obs)
    client.delete(f"/api/v1/observations/{obs.id}", headers=observer_headers)
    assert db.query(Alert).count() == 0


def test_failed_delivery_raises_so_pubsub_retries(db, make_user, org, monkeypatch):
    from app import handlers
    from app.adapters.events import Event

    organisation, headers = org
    monkeypatch.setattr(webhooks, "_resolve", lambda host, port: ["93.184.215.14"])
    from app.models import AlertSettings

    db.add(
        AlertSettings(
            organisation_id=organisation.id,
            webhook_url="https://hooks.example.org/x",
            webhook_secret="whsec_x",
        )
    )
    db.commit()
    monkeypatch.setattr(alerts_module, "send", lambda *a, **k: DeliveryResult(False, 503))
    observer, _ = make_user()
    obs = make_verified(db, observer, taxon(db, "Sciurus carolinensis"), *LONDON, NOW)
    alert = evaluate(db, obs)[0]  # in-process delivery fails and is only logged
    with pytest.raises(RuntimeError, match="503"):
        handlers.deliver_alert(Event(topic="alert.created", payload={"alert_id": str(alert.id)}))
