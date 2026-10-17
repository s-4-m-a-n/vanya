"""Event handlers. Imported at startup so `@subscribe` registers them.

Locally they run in-process; on GCP, Pub/Sub push delivers to /internal/events/pubsub,
which dispatches to the same functions. Every handler is idempotent, so redelivery is safe.
"""

import uuid

from app.adapters.analytics import get_analytics
from app.adapters.events import Event, get_event_bus, subscribe
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Observation
from app.services.alerts import AlertService
from app.services.verification import build_verification_service, build_xp_service


@subscribe("observation.submitted")
def route_submitted_observation(event: Event) -> None:
    with get_sessionmaker()() as db:
        build_verification_service(db).route(uuid.UUID(event.payload["observation_id"]))


@subscribe("observation.in_review")
@subscribe("observation.verified")
@subscribe("observation.rejected")
def sync_rewards(event: Event) -> None:
    with get_sessionmaker()() as db:
        build_xp_service(db).sync(uuid.UUID(event.payload["observation_id"]))


@subscribe("observation.verified")
def export_verified_observation(event: Event) -> None:
    with get_sessionmaker()() as db:
        obs = db.get(Observation, uuid.UUID(event.payload["observation_id"]))
        if obs is None or obs.taxon is None:
            return
        get_analytics().record_verified(
            {
                "observation_id": str(obs.id),
                "taxon_id": obs.taxon.id,
                "scientific_name": obs.taxon.scientific_name,
                "taxon_group": obs.taxon.taxon_group,
                "iucn_status": obs.taxon.iucn_status,
                "latitude": obs.latitude,
                "longitude": obs.longitude,
                "observed_at": obs.observed_at.isoformat(),
                "verified_at": obs.decided_at.isoformat() if obs.decided_at else None,
                "method": obs.verification_method,
                "is_rare": bool(obs.is_rare),
                "rarity_score": obs.rarity_score,
                "habitat": obs.habitat,
            }
        )


@subscribe("observation.deleted")
def forget_deleted_observation(event: Event) -> None:
    get_analytics().forget(event.payload["observation_id"])


@subscribe("observation.verified")
def raise_alerts(event: Event) -> None:
    with get_sessionmaker()() as db:
        AlertService(db, get_event_bus(), get_settings()).evaluate(
            uuid.UUID(event.payload["observation_id"])
        )


@subscribe("alert.created")
def deliver_alert(event: Event) -> None:
    with get_sessionmaker()() as db:
        result = AlertService(db, get_event_bus(), get_settings()).deliver(
            uuid.UUID(event.payload["alert_id"])
        )
    if result is not None and not result.ok:
        # Raising makes Pub/Sub retry with backoff; in-process delivery just logs it.
        raise RuntimeError(f"Webhook delivery failed: {result.error or result.http_status}")
