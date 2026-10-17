import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.adapters.storage import BlobStorage
from app.models import (
    AccessPurpose,
    Geoprivacy,
    Habitat,
    IdCandidate,
    LocationSource,
    Observation,
    ObservationStatus,
    RejectReason,
    Review,
    ReviewDecision,
    Role,
    User,
    VerificationMethod,
)
from app.schemas.taxa import TaxonOut


class CandidateOut(BaseModel):
    rank: int
    scientific_name: str
    common_name: str | None
    confidence: float
    in_scope: bool
    taxon: TaxonOut | None

    @classmethod
    def build(cls, candidate: IdCandidate, locale: str) -> "CandidateOut":
        return cls(
            rank=candidate.rank,
            scientific_name=candidate.scientific_name,
            common_name=candidate.common_name,
            confidence=candidate.confidence,
            in_scope=candidate.in_scope,
            taxon=TaxonOut.build(candidate.taxon, locale) if candidate.taxon else None,
        )


class IdentificationOut(BaseModel):
    provider: str | None
    model: str | None
    is_organism: bool | None
    explanation: str | None
    error: str | None
    latency_ms: int | None
    candidates: list[CandidateOut]


class FlagOut(BaseModel):
    code: str
    severity: str
    detail: str | None = None


class ReviewOut(BaseModel):
    decision: ReviewDecision
    taxon: TaxonOut | None
    reason: RejectReason | None
    comment: str | None
    reviewer_name: str | None
    created_at: datetime

    @classmethod
    def build(cls, review: Review, locale: str) -> "ReviewOut":
        return cls(
            decision=ReviewDecision(review.decision),
            taxon=TaxonOut.build(review.taxon, locale) if review.taxon else None,
            reason=RejectReason(review.reason) if review.reason else None,
            comment=review.comment,
            reviewer_name=review.reviewer.display_name if review.reviewer else None,
            created_at=review.created_at,
        )


class VerificationOut(BaseModel):
    is_rare: bool | None
    required_reviews: int | None
    review_count: int
    method: VerificationMethod | None
    decided_at: datetime | None
    rejection_reason: RejectReason | None
    reviews: list[ReviewOut]
    # Reviewer-only detail. Empty for observers so the checks are harder to game.
    reasons: list[str]
    integrity_flags: list[FlagOut]
    photo_flags: list[str]


class XpEntryOut(BaseModel):
    kind: str
    amount: int
    status: str


class RewardsOut(BaseModel):
    xp: int  # excluding void entries
    status: str | None  # "provisional" | "confirmed" | None (nothing awarded yet)
    entries: list[XpEntryOut]
    rarity_score: float | None
    rarity_tier: str | None
    note: str | None  # "repeat" | "restricted_area" | "daily_cap"

    @classmethod
    def build(cls, obs: Observation) -> "RewardsOut":
        live = [e for e in obs.xp_entries if e.status != "void"]
        statuses = {e.status for e in live}
        return cls(
            xp=sum(e.amount for e in live),
            status="provisional" if "provisional" in statuses else ("confirmed" if live else None),
            entries=[XpEntryOut(kind=e.kind, amount=e.amount, status=e.status) for e in live],
            rarity_score=obs.rarity_score,
            rarity_tier=obs.rarity_tier,
            note=obs.reward_note,
        )


# Roles that may read other people's records (with protected locations generalised).
REVIEW_ROLES = {Role.VERIFIER.value, Role.ANALYST.value, Role.ADMIN.value}


class ObservationOut(BaseModel):
    id: uuid.UUID
    client_uuid: uuid.UUID
    status: ObservationStatus
    taxon: TaxonOut | None
    observer_taxon: TaxonOut | None
    # Exact for the observer and for open records; otherwise the generalised point
    # (see location_precision). Exact coordinates: POST /observations/{id}/exact-location.
    latitude: float | None
    longitude: float | None
    accuracy_m: float | None
    location_precision: str  # "exact" | "generalised"
    geoprivacy: Geoprivacy | None
    observer_geoprivacy: Geoprivacy
    location_source: LocationSource
    observed_at: datetime
    created_at: datetime
    habitat: Habitat | None
    notes: str | None
    photo_url: str
    photo_width: int
    photo_height: int
    identification: IdentificationOut
    verification: VerificationOut
    rewards: RewardsOut

    @classmethod
    def fields_for(cls, obs: Observation, viewer: User, storage: BlobStorage) -> dict[str, Any]:
        locale = viewer.locale
        reviewer_view = viewer.role in REVIEW_ROLES
        exact = obs.user_id == viewer.id or obs.geoprivacy == Geoprivacy.OPEN.value

        def taxon(t):  # noqa: ANN001, ANN202
            return TaxonOut.build(t, locale) if t else None

        return {
            "id": obs.id,
            "client_uuid": obs.client_uuid,
            "status": ObservationStatus(obs.status),
            "taxon": taxon(obs.taxon),
            "observer_taxon": taxon(obs.observer_taxon),
            "latitude": obs.latitude if exact else obs.generalised_latitude,
            "longitude": obs.longitude if exact else obs.generalised_longitude,
            "accuracy_m": obs.accuracy_m if exact else obs.generalised_accuracy_m,
            "location_precision": "exact" if exact else "generalised",
            "geoprivacy": Geoprivacy(obs.geoprivacy) if obs.geoprivacy else None,
            "observer_geoprivacy": Geoprivacy(obs.observer_geoprivacy or "open"),
            "location_source": LocationSource(obs.location_source),
            "observed_at": obs.observed_at,
            "created_at": obs.created_at,
            "habitat": Habitat(obs.habitat) if obs.habitat else None,
            "notes": obs.notes,
            "photo_url": storage.signed_url(obs.photo_key),
            "photo_width": obs.photo_width,
            "photo_height": obs.photo_height,
            "identification": IdentificationOut(
                provider=obs.id_provider,
                model=obs.id_model,
                is_organism=obs.id_is_organism,
                explanation=obs.id_explanation,
                error=obs.id_error,
                latency_ms=obs.id_latency_ms,
                candidates=[CandidateOut.build(c, locale) for c in obs.candidates],
            ),
            "verification": VerificationOut(
                is_rare=obs.is_rare,
                required_reviews=obs.required_reviews,
                review_count=len(obs.reviews),
                method=VerificationMethod(obs.verification_method)
                if obs.verification_method
                else None,
                decided_at=obs.decided_at,
                rejection_reason=RejectReason(obs.rejection_reason)
                if obs.rejection_reason
                else None,
                reviews=[ReviewOut.build(r, locale) for r in obs.reviews],
                reasons=obs.review_reasons if reviewer_view else [],
                integrity_flags=[FlagOut(**f) for f in obs.integrity_flags]
                if reviewer_view
                else [],
                photo_flags=obs.quality_flags if reviewer_view else [],
            ),
            "rewards": RewardsOut.build(obs),
        }

    @classmethod
    def build(cls, obs: Observation, viewer: User, storage: BlobStorage) -> "ObservationOut":
        return cls(**cls.fields_for(obs, viewer, storage))


class ObservationPage(BaseModel):
    items: list[ObservationOut]
    total: int


class SpeciesChoice(BaseModel):
    taxon_id: int


class ObserverOut(BaseModel):
    display_name: str
    reputation: int


class QueueItem(ObservationOut):
    observer: ObserverOut

    @classmethod
    def build_item(cls, obs: Observation, viewer: User, storage: BlobStorage) -> "QueueItem":
        return cls(
            **cls.fields_for(obs, viewer, storage),
            observer=ObserverOut(
                display_name=obs.observer.display_name, reputation=obs.observer.reputation
            ),
        )


class QueuePage(BaseModel):
    items: list[QueueItem]
    total: int


class ReviewIn(BaseModel):
    decision: ReviewDecision
    # Confirm: the species you confirm (defaults to the observer's pick).
    taxon_id: int | None = None
    # Reject: why.
    reason: RejectReason | None = None
    comment: str | None = Field(default=None, max_length=1000)


class ExactLocationRequest(BaseModel):
    purpose: AccessPurpose
    note: str | None = Field(default=None, max_length=500)


class ExactLocationOut(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: float | None
    exif_latitude: float | None
    exif_longitude: float | None
