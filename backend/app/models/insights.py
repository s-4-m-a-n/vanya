import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk, enum_check


class AlertKind(StrEnum):
    INVASIVE = "invasive"  # a watchlisted invasive species was verified in the org's regions
    NEW_RECORD = "new_record"  # first verified record of the species in the org's regions
    PRIORITY = "priority"  # a watchlisted priority species was verified


class AlertSettings(Base):
    """Which alerts an organisation wants, and where to push them (PRD F6)."""

    __tablename__ = "alert_settings"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    notify_invasive: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_new_record: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_priority: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    # Signs webhook bodies (HMAC-SHA256). Move to Secret Manager on GCP.
    webhook_secret: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Alert(UUIDPk, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id", "observation_id", "kind", name="uq_alerts_org_obs_kind"
        ),
        CheckConstraint(enum_check("kind", AlertKind), name="kind"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(12))
    taxon_id: Mapped[int] = mapped_column(ForeignKey("taxa.id"))
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions_of_interest.id", ondelete="SET NULL")
    )
    # Snapshot sent to members and webhooks: species, generalised location, region name.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class AlertDelivery(Base):
    """One webhook delivery attempt."""

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(500))
    ok: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalystQuery(UUIDPk, Base):
    """A natural-language question to the Gemini analyst (PRD F7), with its plan and answer."""

    __tablename__ = "analyst_queries"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(10))
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(300))
    model: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Report(UUIDPk, Base):
    """A periodic report written by the analyst in the reader's language (PRD F7)."""

    __tablename__ = "reports"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    locale: Mapped[str] = mapped_column(String(10))
    # {"title", "summary", "sections": [{"heading", "paragraphs"}], "recommendations"}
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The figures the report was written from, for checking and reuse.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
