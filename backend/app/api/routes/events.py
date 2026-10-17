"""Pub/Sub push delivery (GCP). Mounted only when EVENTS_PROVIDER=pubsub.

Each push subscription targets this endpoint with an OIDC token signed by Google for the
configured service account; the message is dispatched to the same handlers that run
in-process locally.
"""

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.adapters.events import Event, dispatch
from app.config import get_settings

router = APIRouter(tags=["internal"], include_in_schema=False)


class PushMessage(BaseModel):
    data: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    message_id: str = Field(default="", alias="messageId")
    publish_time: str | None = Field(default=None, alias="publishTime")


class PushEnvelope(BaseModel):
    message: PushMessage
    subscription: str = ""


def verify_push_token(authorization: str | None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.pubsub_push_audience:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push endpoint not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")

    from google.auth.transport import requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
            requests.Request(),
            audience=settings.pubsub_push_audience,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    expected = settings.pubsub_push_service_account
    if expected and (claims.get("email") != expected or not claims.get("email_verified")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unexpected caller")
    return claims


@router.post("/internal/events/pubsub", status_code=status.HTTP_204_NO_CONTENT)
def pubsub_push(
    envelope: PushEnvelope, authorization: Annotated[str | None, Header()] = None
) -> None:
    verify_push_token(authorization)
    attributes = envelope.message.attributes
    topic = attributes.get("topic")
    if not topic:
        # Not retryable: acknowledge so Pub/Sub doesn't redeliver forever.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message has no topic attribute")
    try:
        payload = json.loads(base64.b64decode(envelope.message.data or "e30="))
    except (binascii.Error, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed message data") from exc
    published = attributes.get("published_at")
    event = Event(
        topic=topic,
        payload=payload,
        id=attributes.get("event_id") or envelope.message.message_id,
        published_at=datetime.fromisoformat(published) if published else datetime.now(UTC),
    )
    # Handler errors propagate as 500 so Pub/Sub retries with backoff.
    dispatch(event, raise_errors=True)
