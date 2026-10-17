import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk, enum_check
from app.models.taxon import Taxon


class ObservationStatus(StrEnum):
    NEEDS_ID = "needs_id"  # uploaded; waiting for the observer to confirm a species
    SUBMITTED = "submitted"  # species chosen; verification routing pending
    IN_REVIEW = "in_review"  # waiting for expert review
    VERIFIED = "verified"
    REJECTED = "rejected"


class VerificationMethod(StrEnum):
    AUTO = "auto"  # high-confidence common species (PRD F3)
    EXPERT = "expert"


class ReviewDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"


class RejectReason(StrEnum):
    NOT_WILD = "not_wild"  # captive animal or cultivated plant
    WRONG_LOCATION_OR_TIME = "wrong_location_or_time"
    DUPLICATE_OR_STOCK = "duplicate_or_stock"
    UNIDENTIFIABLE = "unidentifiable"
    NOT_AN_ORGANISM = "not_an_organism"
    DISTURBANCE = "disturbance"  # wildlife harassed or nest disturbed (PRD §6)
    INAPPROPRIATE = "inappropriate"


class Habitat(StrEnum):
    FOREST = "forest"
    GRASSLAND = "grassland"
    WETLAND = "wetland"
    FRESHWATER = "freshwater"
    COASTAL_MARINE = "coastal_marine"
    DESERT_SCRUB = "desert_scrub"
    MOUNTAIN = "mountain"
    FARMLAND = "farmland"
    URBAN_GARDEN = "urban_garden"
    OTHER = "other"


class Geoprivacy(StrEnum):
    """How precisely a record's location may be shown to people other than its observer."""

    OPEN = "open"  # shown to ~110 m (rounded to protect observers' homes)
    OBSCURED = "obscured"  # shown as a 0.2° (~22 km) grid cell
    HIDDEN = "hidden"  # never shown publicly; reviewers see a 1° cell


class AccessPurpose(StrEnum):
    VERIFICATION = "verification"
    RESEARCH = "research"
    MANAGEMENT = "management"  # park or reserve management
    ENFORCEMENT = "enforcement"  # anti-poaching, wildlife crime
    OTHER = "other"


class LocationSource(StrEnum):
    DEVICE = "device"
    EXIF = "exif"


class Observation(UUIDPk, Timestamps, Base):
    __tablename__ = "observations"
    __table_args__ = (
        # Offline clients retry uploads; the client-generated id makes them idempotent.
        UniqueConstraint("user_id", "client_uuid", name="uq_observations_user_client_uuid"),
        CheckConstraint(enum_check("status", ObservationStatus), name="status"),
        CheckConstraint(enum_check("habitat", Habitat), name="habitat"),
        CheckConstraint(enum_check("location_source", LocationSource), name="location_source"),
        CheckConstraint(
            enum_check("verification_method", VerificationMethod), name="verification_method"
        ),
        CheckConstraint(enum_check("rejection_reason", RejectReason), name="rejection_reason"),
        CheckConstraint(
            "rarity_tier IN ('common', 'uncommon', 'rare', 'very_rare', 'exceptional')",
            name="rarity_tier",
        ),
        CheckConstraint(enum_check("geoprivacy", Geoprivacy), name="geoprivacy"),
        CheckConstraint("observer_geoprivacy IN ('open', 'obscured')", name="observer_geoprivacy"),
        Index(
            "ix_observations_generalised_location", "generalised_location", postgresql_using="gist"
        ),
        CheckConstraint(
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="coords"
        ),
        Index("ix_observations_location", "location", postgresql_using="gist"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_uuid: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(20), index=True)
    # Current identification: the observer's pick, replaced by expert consensus if they differ.
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxa.id"), index=True)
    # What the observer chose, kept for accuracy metrics and rewards.
    observer_taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxa.id"))

    # Plain lat/lon are the source of truth (portable to BigQuery's ST_GEOGPOINT);
    # PostGIS derives the indexed geometry for spatial queries.
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    location: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False),
        Computed("ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)", persisted=True),
    )
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    location_source: Mapped[str] = mapped_column(String(10))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    habitat: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)

    photo_key: Mapped[str] = mapped_column(String(300))
    photo_sha256: Mapped[str] = mapped_column(String(64), index=True)
    photo_width: Mapped[int] = mapped_column(Integer)
    photo_height: Mapped[int] = mapped_column(Integer)
    # EXIF from the original upload (GPS, capture time, camera); stored copies are stripped.
    exif: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    id_provider: Mapped[str | None] = mapped_column(String(20))
    id_model: Mapped[str | None] = mapped_column(String(100))
    id_is_organism: Mapped[bool | None] = mapped_column(Boolean)
    id_explanation: Mapped[str | None] = mapped_column(Text)
    id_error: Mapped[str | None] = mapped_column(String(300))
    id_latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Identifier's photo-level warnings, e.g. "screenshot", "captive_or_cultivated".
    quality_flags: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # 64-bit difference hash for near-duplicate detection (signed to fit BIGINT).
    photo_dhash: Mapped[int | None] = mapped_column(BigInteger)

    # --- Verification (PRD F3) ---
    # [{"code": ..., "severity": "hard" | "soft", "detail": ...}]
    integrity_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Why the record needs an expert, e.g. ["low_confidence", "threatened_species"].
    review_reasons: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    is_rare: Mapped[bool | None] = mapped_column(Boolean)
    required_reviews: Mapped[int | None] = mapped_column(SmallInteger)
    verification_method: Mapped[str | None] = mapped_column(String(10))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(30))

    # --- Rewards (PRD F2, §6) ---
    rarity_score: Mapped[float | None] = mapped_column(Float)
    rarity_tier: Mapped[str | None] = mapped_column(String(12))
    # Why XP was reduced or withheld: "repeat", "restricted_area", "daily_cap".
    reward_note: Mapped[str | None] = mapped_column(String(20))

    # --- Location protection (PRD F5) ---
    # The observer's own choice; the effective level is the stricter of this and the
    # species' sensitivity.
    observer_geoprivacy: Mapped[str] = mapped_column(
        String(10), default=Geoprivacy.OPEN.value, server_default=Geoprivacy.OPEN.value
    )
    geoprivacy: Mapped[str | None] = mapped_column(String(10))
    # Coordinates safe to use outside the audited exact-location endpoint: in public
    # views, leaderboards and queue filters. Precision depends on `geoprivacy`.
    generalised_latitude: Mapped[float | None] = mapped_column(Float)
    generalised_longitude: Mapped[float | None] = mapped_column(Float)
    generalised_accuracy_m: Mapped[float | None] = mapped_column(Float)
    generalised_location: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False),
        Computed(
            "ST_SetSRID(ST_MakePoint(generalised_longitude, generalised_latitude), 4326)",
            persisted=True,
        ),
        nullable=True,
    )

    taxon: Mapped[Taxon | None] = relationship(lazy="joined", foreign_keys=[taxon_id])
    observer_taxon: Mapped[Taxon | None] = relationship(
        lazy="joined", foreign_keys=[observer_taxon_id]
    )
    observer: Mapped["User"] = relationship(foreign_keys=[user_id])  # noqa: F821
    candidates: Mapped[list["IdCandidate"]] = relationship(
        back_populates="observation",
        order_by="IdCandidate.rank",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="observation",
        order_by="Review.created_at",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    xp_entries: Mapped[list["XpEntry"]] = relationship(  # noqa: F821
        order_by="XpEntry.id", lazy="selectin", viewonly=True
    )


class IdCandidate(Base):
    """One ranked species suggestion from the identifier for an observation."""

    __tablename__ = "id_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(SmallInteger)
    scientific_name: Mapped[str] = mapped_column(String(200))
    common_name: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float)
    # Resolved against the taxa table; null when the name could not be matched.
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxa.id"))
    # False for taxa outside v1 scope (insects, fish, fungi; PRD §3 non-goals).
    in_scope: Mapped[bool] = mapped_column(Boolean)

    observation: Mapped[Observation] = relationship(back_populates="candidates")
    taxon: Mapped[Taxon | None] = relationship(lazy="joined")


class Review(Base):
    """One expert's verdict on an observation."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("observation_id", "reviewer_id", name="uq_reviews_observation_reviewer"),
        CheckConstraint(enum_check("decision", ReviewDecision), name="decision"),
        CheckConstraint(enum_check("reason", RejectReason), name="reason"),
        CheckConstraint(
            "(decision = 'confirm' AND taxon_id IS NOT NULL AND reason IS NULL)"
            " OR (decision = 'reject' AND reason IS NOT NULL)",
            name="decision_fields",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True
    )
    # Kept when a reviewer deletes their account so the decision history survives.
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decision: Mapped[str] = mapped_column(String(10))
    # The species the expert confirms; may differ from the observer's pick.
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxa.id"))
    reason: Mapped[str | None] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    observation: Mapped[Observation] = relationship(back_populates="reviews")
    taxon: Mapped[Taxon | None] = relationship(lazy="joined")
    reviewer: Mapped["User | None"] = relationship(lazy="joined")  # noqa: F821


class OccurrenceCount(Base):
    """Cached GBIF occurrence counts per species and area: the regional commonness baseline."""

    __tablename__ = "occurrence_counts"

    taxon_id: Mapped[int] = mapped_column(
        ForeignKey("taxa.id", ondelete="CASCADE"), primary_key=True
    )
    # e.g. "local:27.5:85.0" (±0.5° box) or "range:27.5:85.0" (±2.5° box)
    cell: Mapped[str] = mapped_column(String(40), primary_key=True)
    gbif_count: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocationAccess(Base):
    """Audit log of every exact-coordinate read by someone other than the observer.

    No foreign keys: the record must outlive deleted users and observations.
    """

    __tablename__ = "location_access_log"
    __table_args__ = (
        CheckConstraint(enum_check("purpose", AccessPurpose), name="purpose"),
        Index("ix_location_access_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID]
    user_email: Mapped[str] = mapped_column(String(320))
    user_role: Mapped[str] = mapped_column(String(20))
    observation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    taxon_id: Mapped[int | None] = mapped_column(Integer)
    geoprivacy: Mapped[str | None] = mapped_column(String(10))
    purpose: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
