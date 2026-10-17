import base64
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.adapters.events import subscribe
from app.config import get_settings
from app.main import create_app

pytest.importorskip("google.oauth2.id_token")

URL = "/internal/events/pubsub"
CALLER = "pubsub-push@dh.iam.gserviceaccount.com"


@pytest.fixture
def push_client(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "events_provider", "pubsub")
    monkeypatch.setattr(settings, "pubsub_push_audience", "https://api.example/internal")
    monkeypatch.setattr(settings, "pubsub_push_service_account", CALLER)
    return TestClient(create_app())


def envelope(topic: str | None, payload: dict) -> dict:
    attributes = {"event_id": "e1", "published_at": "2026-10-17T00:00:00+00:00"}
    if topic:
        attributes["topic"] = topic
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": data, "attributes": attributes, "messageId": "m1"}}


def claims(email=CALLER, verified=True):
    return mock.patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value={"email": email, "email_verified": verified},
    )


AUTH = {"Authorization": "Bearer signed-by-google"}


def test_push_dispatches_to_the_same_handlers(push_client):
    received = []
    subscribe("test.push")(lambda e: received.append((e.id, e.payload)))
    with claims() as verify:
        res = push_client.post(URL, json=envelope("test.push", {"x": 1}), headers=AUTH)
    assert res.status_code == 204
    assert received == [("e1", {"x": 1})]
    assert verify.call_args.kwargs["audience"] == "https://api.example/internal"


def test_push_rejects_missing_or_foreign_tokens(push_client):
    body = envelope("observation.verified", {})
    assert push_client.post(URL, json=body).status_code == 401
    with claims(email="someone@else.iam.gserviceaccount.com"):
        assert push_client.post(URL, json=body, headers=AUTH).status_code == 403
    with mock.patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError):
        assert push_client.post(URL, json=body, headers=AUTH).status_code == 401


def test_message_without_topic_is_not_retried(push_client):
    with claims():
        assert push_client.post(URL, json=envelope(None, {}), headers=AUTH).status_code == 400


def test_handler_failure_returns_error_so_pubsub_retries(push_client):
    def broken(event):
        raise RuntimeError("database down")

    subscribe("test.broken")(broken)
    with claims(), pytest.raises(RuntimeError):
        push_client.post(URL, json=envelope("test.broken", {}), headers=AUTH)


def test_endpoint_is_absent_with_in_process_events(client):
    assert client.post(URL, json=envelope("x", {})).status_code == 404
