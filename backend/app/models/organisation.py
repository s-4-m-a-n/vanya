import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPk, enum_check
from app.models.taxon import Taxon


class OrganisationKind(StrEnum):
    AGENCY = "agency"
    PARK = "park"
    NGO = "ngo"
    RESEARCH = "research"
    OTHER = "other"


class MemberRole(StrEnum):
    ADMIN = "admin"  # manages members and regions
    ANALYST = "analyst"  # dashboard, watchlist, audited exact locations in the org's regions


class WatchCategory(StrEnum):
    INVASIVE = "invasive"  # invasive here: drives the invasive panel and Stage 6 alerts
    PRIORITY = "priority"  # species the organisation monitors closely


class Organisation(UUIDPk, Base):
    """A conservation agency, park, NGO or research group (PRD §4) with its own workspace."""

    __tablename__ = "organisations"
    __table_args__ = (CheckConstraint(enum_check("kind", OrganisationKind), name="kind"),)

    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["OrganisationMember"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan", lazy="selectin"
    )
    regions: Mapped[list["RegionOfInterest"]] = relationship(
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RegionOfInterest.name",
    )


class OrganisationMember(Base):
    __tablename__ = "organisation_members"
    __table_args__ = (CheckConstraint(enum_check("role", MemberRole), name="role"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organisation: Mapped[Organisation] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(lazy="joined")  # noqa: F821


class RegionOfInterest(Base):
    __tablename__ = "regions_of_interest"
    __table_args__ = (Index("ix_regions_of_interest_area", "area", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    area: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organisation: Mapped[Organisation] = relationship(back_populates="regions")


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"
    __table_args__ = (CheckConstraint(enum_check("category", WatchCategory), name="category"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    taxon_id: Mapped[int] = mapped_column(
        ForeignKey("taxa.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    taxon: Mapped[Taxon] = relationship(lazy="joined")
