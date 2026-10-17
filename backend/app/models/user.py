from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class Role(StrEnum):
    OBSERVER = "observer"  # casual observers and dedicated naturalists
    VERIFIER = "verifier"  # expert reviewers
    ANALYST = "analyst"  # organisation analysts (agency, park, NGO, researcher)
    ADMIN = "admin"


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN ({', '.join(repr(r.value) for r in Role)})", name="role"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.OBSERVER.value)
    locale: Mapped[str] = mapped_column(String(10), default="en")
    # Moves up or down with verification outcomes (PRD §6).
    reputation: Mapped[int] = mapped_column(Integer, default=0)
    # Capture is blocked until the user consents to location recording (PRD §8).
    location_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Verifier preferences for their review queue (PRD §4: "their region and taxon").
    review_taxon_groups: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    review_latitude: Mapped[float | None] = mapped_column(Float)
    review_longitude: Mapped[float | None] = mapped_column(Float)
    review_radius_km: Mapped[float | None] = mapped_column(Float)
    # Leaderboards show only the display name and level; users can hide themselves.
    leaderboard_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
