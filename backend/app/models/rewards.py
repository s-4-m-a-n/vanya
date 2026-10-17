import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, enum_check


class RarityTier(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    EXCEPTIONAL = "exceptional"


class XpKind(StrEnum):
    BASE = "base"
    RARITY = "rarity"  # rare-find bonus, capped per day (PRD §6)
    FIRST_SPECIES = "first_species"
    EXPLORER = "explorer"  # under-sampled area (PRD goal 2)


class XpStatus(StrEnum):
    PROVISIONAL = "provisional"  # observation still in review
    CONFIRMED = "confirmed"  # observation verified
    VOID = "void"  # observation rejected, or the award no longer applies


class AreaGroupCount(Base):
    """Cached GBIF figures for a whole group (e.g. all birds) in a grid cell."""

    __tablename__ = "area_group_counts"

    cell: Mapped[str] = mapped_column(String(40), primary_key=True)
    group: Mapped[str] = mapped_column(String(20), primary_key=True)  # Aves, Mammalia, ...
    gbif_count: Mapped[int] = mapped_column(Integer)
    # Records of the group's most-recorded species here: the frequency reference.
    top_species_count: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RarityScore(Base):
    """How rare a species is in a ~55 km cell (PRD F2), recomputed on a schedule."""

    __tablename__ = "rarity_scores"
    __table_args__ = (CheckConstraint(enum_check("tier", RarityTier), name="tier"),)

    taxon_id: Mapped[int] = mapped_column(
        ForeignKey("taxa.id", ondelete="CASCADE"), primary_key=True
    )
    cell: Mapped[str] = mapped_column(String(40), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(12))
    # Components, kept so the score can be explained.
    frequency: Mapped[float | None] = mapped_column(Float)
    status: Mapped[float] = mapped_column(Float)
    endemism: Mapped[float | None] = mapped_column(Float)
    local_count: Mapped[int | None] = mapped_column(Integer)
    group_total: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class XpEntry(Base):
    """Append-and-update XP ledger: one row per observation and kind of award."""

    __tablename__ = "xp_entries"
    __table_args__ = (
        UniqueConstraint("observation_id", "kind", name="uq_xp_entries_observation_kind"),
        CheckConstraint(enum_check("kind", XpKind), name="kind"),
        CheckConstraint(enum_check("status", XpStatus), name="status"),
        Index("ix_xp_entries_status_confirmed_at", "status", "confirmed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserBadge(Base):
    __tablename__ = "user_badges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    badge: Mapped[str] = mapped_column(String(30), primary_key=True)
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL")
    )


class RestrictedArea(Base):
    """Places where records earn no rewards (PRD §6): reserves closed to the public,
    breeding colonies, private land. Records there are still kept as data."""

    __tablename__ = "restricted_areas"
    __table_args__ = (Index("ix_restricted_areas_area", "area", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(300))
    area: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
