"""Verification pipeline (PRD F3, §6): route submissions, collect reviews, settle outcomes."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select, update
from sqlalchemy.orm import Session

from app.adapters.events import EventBus, get_event_bus
from app.config import Settings, get_settings
from app.models import (
    Observation,
    ObservationStatus,
    RejectReason,
    Review,
    ReviewDecision,
    Taxon,
    User,
    VerificationMethod,
)
from app.services.commonness import CommonnessService, OccurrenceSource
from app.services.gbif import get_gbif_client
from app.services.geoprivacy import apply_geoprivacy
from app.services.integrity import DUPLICATE_CODES, Flag, duplicate_flags
from app.services.observations import ObservationError
from app.services.rarity import RarityService
from app.services.routing import RoutingInput, decide_route, is_rare_status, tally
from app.services.taxon_status import TaxonStatusService
from app.services.xp import XpService

logger = logging.getLogger(__name__)

# Reputation moves with verification outcomes (PRD §6).
REPUTATION = {
    "auto": 1,
    "expert": 2,
    "expert_rare": 5,
    "corrected": 0,  # verified, but as a different species than the observer chose
    "duplicate_own": 0,  # an honest double upload isn't penalised
    "rejected": -3,
    "rejected_fraud": -10,
}
FRAUD_REASONS = {RejectReason.DUPLICATE_OR_STOCK, RejectReason.WRONG_LOCATION_OR_TIME}


class VerificationService:
    def __init__(
        self,
        db: Session,
        events: EventBus,
        commonness: CommonnessService,
        settings: Settings,
        statuses: TaxonStatusService | None = None,
    ) -> None:
        self.db = db
        self.events = events
        self.commonness = commonness
        self.settings = settings
        self.statuses = statuses
        self._outbox: list[tuple[str, dict[str, Any]]] = []

    # --- Routing -----------------------------------------------------------------------

    def route(self, observation_id: uuid.UUID) -> Observation | None:
        """Decide auto-accept vs expert review for a newly submitted observation."""
        obs = self._lock(observation_id)
        if obs is None or obs.status != ObservationStatus.SUBMITTED.value or obs.taxon is None:
            self.db.rollback()
            return obs

        # A species added from GBIF has no checked status yet; look it up first so a
        # threatened species is both routed and protected as one.
        if self.statuses is not None and obs.taxon.status_source is None:
            self.statuses.refresh(obs.taxon)
        apply_geoprivacy(obs, obs.taxon)

        capture = [
            Flag.from_json(f) for f in obs.integrity_flags if f["code"] not in DUPLICATE_CODES
        ]
        flags = capture + duplicate_flags(self.db, obs, self.settings.near_duplicate_max_bits)
        commonness = self.commonness.assess(obs.taxon, obs.latitude, obs.longitude, obs.id)
        route = decide_route(
            RoutingInput(
                chosen_taxon_id=obs.taxon.id,
                iucn_status=obs.taxon.iucn_status,
                candidates=[(c.taxon_id, c.confidence) for c in obs.candidates],
                id_is_organism=obs.id_is_organism,
                commonness=commonness,
                flags=flags,
            ),
            self.settings.auto_accept_min_confidence,
        )

        obs.integrity_flags = [f.as_json() for f in flags]
        obs.review_reasons = route.reasons
        obs.is_rare = route.is_rare
        obs.required_reviews = route.required_reviews
        if route.status == ObservationStatus.IN_REVIEW:
            obs.status = route.status.value
            self._outbox.append(
                (
                    "observation.in_review",
                    {
                        "observation_id": str(obs.id),
                        "is_rare": route.is_rare,
                        "required_reviews": route.required_reviews,
                    },
                )
            )
        else:
            self._settle(
                obs,
                route.status,
                VerificationMethod.AUTO,
                obs.taxon.id,
                route.rejection_reason,
                "duplicate_own" if route.status == ObservationStatus.REJECTED else "auto",
            )
        self._commit()
        return obs

    # --- Expert review -----------------------------------------------------------------

    def review(
        self,
        observation_id: uuid.UUID,
        reviewer: User,
        decision: ReviewDecision,
        taxon_id: int | None = None,
        reason: RejectReason | None = None,
        comment: str | None = None,
    ) -> Observation:
        obs = self._lock(observation_id)
        if obs is None:
            raise ObservationError("not_found", "Observation not found", status_code=404)
        if obs.user_id == reviewer.id:
            raise ObservationError(
                "own_observation", "You can't review your own observation", status_code=403
            )
        if obs.status != ObservationStatus.IN_REVIEW.value:
            raise ObservationError("not_in_review", "Not waiting for review", status_code=409)
        if any(r.reviewer_id == reviewer.id for r in obs.reviews):
            raise ObservationError("already_reviewed", "Already reviewed", status_code=409)

        if decision == ReviewDecision.CONFIRM:
            taxon = self.db.get(Taxon, taxon_id or obs.taxon_id)
            if taxon is None:
                raise ObservationError("unknown_taxon", f"Unknown taxon {taxon_id}")
            taxon_id, reason = taxon.id, None
            # An expert re-identifying it as a threatened species makes it a rare claim.
            if taxon.id != obs.taxon_id and is_rare_status(taxon.iucn_status):
                obs.is_rare = True
                obs.required_reviews = max(obs.required_reviews or 1, 2)
        else:
            if reason is None:
                raise ObservationError("reason_required", "Give a reason for rejecting")
            taxon_id = None

        obs.reviews.append(
            Review(
                reviewer_id=reviewer.id,
                decision=decision.value,
                taxon_id=taxon_id,
                reason=reason.value if reason else None,
                comment=comment,
            )
        )
        self.db.flush()

        result = tally(
            [(r.decision, r.taxon_id, r.reason) for r in obs.reviews], obs.required_reviews or 1
        )
        if result.status == ObservationStatus.VERIFIED:
            final_taxon = result.taxon_id or obs.taxon_id
            if final_taxon != obs.observer_taxon_id:
                key = "corrected"
            else:
                key = "expert_rare" if obs.is_rare else "expert"
            self._settle(obs, result.status, VerificationMethod.EXPERT, final_taxon, None, key)
        elif result.status == ObservationStatus.REJECTED:
            key = "rejected_fraud" if result.rejection_reason in FRAUD_REASONS else "rejected"
            self._settle(
                obs, result.status, VerificationMethod.EXPERT, None, result.rejection_reason, key
            )
        self._commit()
        return obs

    def queue(
        self,
        reviewer: User,
        group: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        anywhere: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Observation], int]:
        """Records waiting for this reviewer: rare claims first, then oldest first."""
        already_reviewed = (
            select(Review.id)
            .where(Review.observation_id == Observation.id, Review.reviewer_id == reviewer.id)
            .exists()
        )
        stmt = (
            select(Observation)
            .join(Taxon, Observation.taxon_id == Taxon.id)
            .where(
                Observation.status == ObservationStatus.IN_REVIEW.value,
                Observation.user_id != reviewer.id,
                ~already_reviewed,
            )
        )
        groups = [group] if group else reviewer.review_taxon_groups
        if groups:
            stmt = stmt.where(Taxon.taxon_group.in_(groups))

        if latitude is None and not anywhere:
            latitude, longitude, radius_km = (
                reviewer.review_latitude,
                reviewer.review_longitude,
                reviewer.review_radius_km,
            )
        if latitude is not None and longitude is not None and radius_km:
            # Generalised points, so shrinking the area can't pinpoint protected records.
            point = cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography)
            stmt = stmt.where(
                func.ST_DWithin(
                    cast(Observation.generalised_location, Geography), point, radius_km * 1000
                )
            )

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.scalars(
            stmt.order_by(Observation.is_rare.desc().nulls_last(), Observation.created_at)
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), total

    # --- Internals ---------------------------------------------------------------------

    def _lock(self, observation_id: uuid.UUID) -> Observation | None:
        # Serialises routing and reviews per observation so tallies never race.
        return self.db.scalar(
            select(Observation)
            .where(Observation.id == observation_id)
            .with_for_update(of=Observation)
        )

    def _settle(
        self,
        obs: Observation,
        status: ObservationStatus,
        method: VerificationMethod,
        taxon_id: int | None,
        rejection_reason: RejectReason | None,
        reputation_key: str,
    ) -> None:
        obs.status = status.value
        obs.verification_method = method.value
        obs.decided_at = datetime.now(UTC)
        obs.rejection_reason = rejection_reason.value if rejection_reason else None
        if taxon_id is not None and taxon_id != obs.taxon_id:
            # Assign the object, not just the id, so the loaded relationship stays in sync.
            obs.taxon = self.db.get(Taxon, taxon_id)
            if self.statuses is not None and obs.taxon.status_source is None:
                self.statuses.refresh(obs.taxon)
            apply_geoprivacy(obs, obs.taxon)
            # Rarity was judged for the claimed species; re-derive it for the confirmed one.
            # (Stage 3's rarity score recomputes this fully, including local commonness.)
            obs.is_rare = is_rare_status(obs.taxon.iucn_status)
        delta = REPUTATION[reputation_key]
        if delta:
            self.db.execute(
                update(User)
                .where(User.id == obs.user_id)
                .values(reputation=User.reputation + delta)
            )
        payload = {
            "observation_id": str(obs.id),
            "user_id": str(obs.user_id),
            "method": method.value,
            "is_rare": bool(obs.is_rare),
        }
        if status == ObservationStatus.VERIFIED:
            self._outbox.append(("observation.verified", {**payload, "taxon_id": obs.taxon_id}))
        else:
            self._outbox.append(
                ("observation.rejected", {**payload, "reason": obs.rejection_reason})
            )

    def _commit(self) -> None:
        self.db.commit()
        outbox, self._outbox = self._outbox, []
        for topic, payload in outbox:
            self.events.publish(topic, payload)


def get_occurrence_source() -> OccurrenceSource | None:
    if get_settings().occurrence_baseline != "gbif":
        return None
    return get_gbif_client(background=True)


def build_commonness_service(db: Session) -> CommonnessService:
    settings = get_settings()
    return CommonnessService(
        db,
        get_occurrence_source(),
        settings.common_min_gbif_records,
        settings.common_min_verified_records,
        settings.occurrence_cache_days,
    )


def build_status_service(db: Session) -> TaxonStatusService:
    return TaxonStatusService(db, build_commonness_service(db), get_occurrence_source())


def build_verification_service(db: Session) -> VerificationService:
    return VerificationService(
        db,
        get_event_bus(),
        build_commonness_service(db),
        get_settings(),
        build_status_service(db),
    )


def build_rarity_service(db: Session) -> RarityService:
    return RarityService(db, build_commonness_service(db), get_occurrence_source())


def build_xp_service(db: Session) -> XpService:
    return XpService(db, build_rarity_service(db))
