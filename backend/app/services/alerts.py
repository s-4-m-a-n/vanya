"""Invasive, priority and new-record alerts to organisations (PRD F6).

Triggered when a record is verified. An organisation is alerted when the record's
generalised point falls inside one of its regions, so alerts never reveal more location
detail than the record's protection level allows.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.adapters.events import EventBus
from app.config import Settings
from app.models import (
    Alert,
    AlertDelivery,
    AlertKind,
    AlertSettings,
    Observation,
    ObservationStatus,
    Organisation,
    RegionOfInterest,
    User,
    WatchCategory,
    WatchlistEntry,
)
from app.services.observations import ObservationError
from app.services.webhooks import DeliveryResult, new_secret, send, validate_webhook_url

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self, db: Session, events: EventBus, settings: Settings) -> None:
        self.db = db
        self.events = events
        self.allow_local_webhooks = settings.app_env in ("local", "test")

    # --- Raising alerts ------------------------------------------------------------------

    def evaluate(self, observation_id: uuid.UUID) -> list[Alert]:
        obs = self.db.get(Observation, observation_id)
        if (
            obs is None
            or obs.status != ObservationStatus.VERIFIED.value
            or obs.taxon is None
            or obs.generalised_location is None
        ):
            return []
        regions = self.db.execute(
            select(RegionOfInterest.organisation_id, RegionOfInterest.id, RegionOfInterest.name)
            .where(func.ST_Intersects(RegionOfInterest.area, obs.generalised_location))
            .order_by(RegionOfInterest.organisation_id, RegionOfInterest.name)
        ).all()
        first_region: dict[uuid.UUID, tuple[int, str]] = {}
        for org_id, region_id, name in regions:
            first_region.setdefault(org_id, (region_id, name))

        created: list[Alert] = []
        for org_id, (region_id, region_name) in first_region.items():
            prefs = self.settings_for(org_id)
            watch = self.db.get(WatchlistEntry, (org_id, obs.taxon_id))
            kinds = []
            if watch and watch.category == WatchCategory.INVASIVE.value and prefs.notify_invasive:
                kinds.append(AlertKind.INVASIVE)
            if watch and watch.category == WatchCategory.PRIORITY.value and prefs.notify_priority:
                kinds.append(AlertKind.PRIORITY)
            if prefs.notify_new_record and self._first_in_regions(org_id, obs):
                kinds.append(AlertKind.NEW_RECORD)
            for kind in kinds:
                payload = self._payload(obs, kind, org_id, region_id, region_name)
                alert_id = self.db.scalar(
                    insert(Alert)
                    .values(
                        id=uuid.uuid4(),
                        organisation_id=org_id,
                        observation_id=obs.id,
                        kind=kind.value,
                        taxon_id=obs.taxon_id,
                        region_id=region_id,
                        payload=payload,
                        observed_at=obs.observed_at,
                        verified_at=obs.decided_at,
                    )
                    .on_conflict_do_nothing()  # redelivered events don't duplicate alerts
                    .returning(Alert.id)
                )
                if alert_id is not None:
                    created.append(self.db.get(Alert, alert_id))
        self.db.commit()
        for alert in created:
            self.events.publish("alert.created", {"alert_id": str(alert.id)})
        return created

    def _first_in_regions(self, org_id: uuid.UUID, obs: Observation) -> bool:
        other = aliased(Observation)
        return not self.db.scalar(
            select(
                exists().where(
                    other.taxon_id == obs.taxon_id,
                    other.id != obs.id,
                    other.status == ObservationStatus.VERIFIED.value,
                    exists().where(
                        RegionOfInterest.organisation_id == org_id,
                        func.ST_Intersects(RegionOfInterest.area, other.generalised_location),
                    ),
                )
            )
        )

    def _payload(
        self, obs: Observation, kind: AlertKind, org_id: uuid.UUID, region_id: int, region: str
    ) -> dict[str, Any]:
        taxon = obs.taxon
        return {
            "kind": kind.value,
            "organisation_id": str(org_id),
            "region": {"id": region_id, "name": region},
            "observation_id": str(obs.id),
            "taxon": {
                "id": taxon.id,
                "scientific_name": taxon.scientific_name,
                "common_name": taxon.common_name("en"),
                "taxon_group": taxon.taxon_group,
                "iucn_status": taxon.iucn_status,
            },
            "observed_at": obs.observed_at.isoformat(),
            "verified_at": obs.decided_at.isoformat() if obs.decided_at else None,
            # Generalised: exact coordinates stay behind the audited endpoint.
            "location": {
                "latitude": obs.generalised_latitude,
                "longitude": obs.generalised_longitude,
                "accuracy_m": obs.generalised_accuracy_m,
                "precision": "approximate" if obs.geoprivacy == "open" else "generalised",
            },
            "rarity_tier": obs.rarity_tier,
        }

    # --- Delivery ------------------------------------------------------------------------

    def deliver(self, alert_id: uuid.UUID) -> DeliveryResult | None:
        """Push an alert to the organisation's webhook, if it has one."""
        alert = self.db.get(Alert, alert_id)
        if alert is None:
            return None
        prefs = self.db.get(AlertSettings, alert.organisation_id)
        if prefs is None or not prefs.webhook_url or not prefs.webhook_secret:
            return None
        body = {
            "type": "alert",
            "id": str(alert.id),
            "created_at": alert.created_at.isoformat(),
            **alert.payload,
        }
        return self._send(alert.organisation_id, alert.id, prefs, body)

    def test_webhook(self, org: Organisation) -> DeliveryResult:
        prefs = self.settings_for(org.id)
        if not prefs.webhook_url or not prefs.webhook_secret:
            raise ObservationError("no_webhook", "Set a webhook URL first")
        return self._send(org.id, None, prefs, {"type": "test", "organisation_id": str(org.id)})

    def _send(
        self, org_id: uuid.UUID, alert_id: uuid.UUID | None, prefs: AlertSettings, body: dict
    ) -> DeliveryResult:
        result = send(prefs.webhook_url, prefs.webhook_secret, body, self.allow_local_webhooks)
        self.db.add(
            AlertDelivery(
                alert_id=alert_id,
                organisation_id=org_id,
                url=prefs.webhook_url,
                ok=result.ok,
                http_status=result.http_status,
                error=result.error,
            )
        )
        self.db.commit()
        if not result.ok:
            logger.warning("Webhook delivery to %s failed: %s", prefs.webhook_url, result.error)
        return result

    # --- Inbox and settings --------------------------------------------------------------

    def settings_for(self, org_id: uuid.UUID) -> AlertSettings:
        prefs = self.db.get(AlertSettings, org_id)
        if prefs is None:
            prefs = AlertSettings(
                organisation_id=org_id,
                notify_invasive=True,
                notify_new_record=True,
                notify_priority=True,
            )
        return prefs

    def update_settings(self, org: Organisation, changes: dict[str, Any]) -> AlertSettings:
        prefs = self.db.get(AlertSettings, org.id)
        if prefs is None:
            prefs = self.settings_for(org.id)
            self.db.add(prefs)
        for key in ("notify_invasive", "notify_new_record", "notify_priority"):
            if changes.get(key) is not None:
                setattr(prefs, key, changes[key])
        if "webhook_url" in changes:
            url = changes["webhook_url"] or None
            if url:
                try:
                    validate_webhook_url(url, self.allow_local_webhooks)
                except ValueError as exc:
                    raise ObservationError("unsafe_webhook", str(exc)) from exc
            prefs.webhook_url = url
            if url and not prefs.webhook_secret:
                prefs.webhook_secret = new_secret()
        self.db.commit()
        return prefs

    def rotate_secret(self, org: Organisation) -> str:
        prefs = self.db.get(AlertSettings, org.id)
        if prefs is None or not prefs.webhook_url:
            raise ObservationError("no_webhook", "Set a webhook URL first")
        prefs.webhook_secret = new_secret()
        self.db.commit()
        return prefs.webhook_secret

    def inbox(self, org_id: uuid.UUID, unread_only: bool, limit: int) -> tuple[list[Alert], int]:
        stmt = select(Alert).where(Alert.organisation_id == org_id)
        if unread_only:
            stmt = stmt.where(Alert.acknowledged_at.is_(None))
        unread = self.db.scalar(
            select(func.count()).where(
                Alert.organisation_id == org_id, Alert.acknowledged_at.is_(None)
            )
        )
        rows = self.db.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit)).all()
        return list(rows), unread or 0

    def acknowledge(self, org_id: uuid.UUID, alert_id: uuid.UUID, user: User) -> Alert:
        alert = self.db.get(Alert, alert_id)
        if alert is None or alert.organisation_id != org_id:
            raise ObservationError("not_found", "Alert not found", 404)
        if alert.acknowledged_at is None:
            alert.acknowledged_at = func.now()
            alert.acknowledged_by = user.id
            self.db.commit()
            self.db.refresh(alert)
        return alert
