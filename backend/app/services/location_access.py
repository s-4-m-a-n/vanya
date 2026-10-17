"""Exact coordinates for people other than the observer: role-gated and audit-logged."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AccessPurpose, LocationAccess, Observation, ObservationStatus, Role, User
from app.services.observations import ObservationError
from app.services.organisations import OrganisationService

# Deters bulk harvesting of sensitive locations by a compromised or rogue account.
DAILY_REVEAL_LIMIT = 100


def can_reveal(viewer: User, obs: Observation) -> bool:
    """Who may see exact coordinates, and when (by role and record status):
    - the observer: always;
    - admins: any record;
    - verifiers: only while the record is waiting for review;
    - analysts: only verified records, and only inside a region of one of their
      organisations (checked separately, in LocationAccessService.reveal).
    """
    if obs.user_id == viewer.id or viewer.role == Role.ADMIN.value:
        return True
    if viewer.role == Role.VERIFIER.value:
        return obs.status == ObservationStatus.IN_REVIEW.value
    if viewer.role == Role.ANALYST.value:
        return obs.status == ObservationStatus.VERIFIED.value
    return False


@dataclass(frozen=True)
class ExactLocation:
    latitude: float
    longitude: float
    accuracy_m: float | None
    # Where the photo's own GPS says it was taken, if it differs (helps verification).
    exif_latitude: float | None
    exif_longitude: float | None


class LocationAccessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def reveal(
        self,
        obs: Observation,
        viewer: User,
        purpose: AccessPurpose,
        note: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> ExactLocation:
        if not can_reveal(viewer, obs) or (
            viewer.role == Role.ANALYST.value
            and obs.user_id != viewer.id
            and not OrganisationService(self.db).covers(viewer, obs)
        ):
            raise ObservationError(
                "reveal_not_allowed", "You can't see this exact location", status_code=403
            )
        if obs.user_id != viewer.id:
            if viewer.role != Role.ADMIN.value and self.reveals_today(viewer) >= DAILY_REVEAL_LIMIT:
                raise ObservationError(
                    "reveal_limit", "Daily limit for exact locations reached", status_code=429
                )
            self.db.add(
                LocationAccess(
                    user_id=viewer.id,
                    user_email=viewer.email,
                    user_role=viewer.role,
                    observation_id=obs.id,
                    taxon_id=obs.taxon_id,
                    geoprivacy=obs.geoprivacy,
                    purpose=purpose.value,
                    note=note,
                    ip=ip,
                    user_agent=(user_agent or "")[:300] or None,
                )
            )
            self.db.commit()
        exif = obs.exif or {}
        return ExactLocation(
            obs.latitude, obs.longitude, obs.accuracy_m, exif.get("latitude"), exif.get("longitude")
        )

    def reveals_today(self, viewer: User) -> int:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.scalar(
                select(func.count()).where(
                    LocationAccess.user_id == viewer.id, LocationAccess.created_at >= midnight
                )
            )
            or 0
        )

    def log(self, observation_id=None, user_id=None, limit: int = 100) -> list[LocationAccess]:  # noqa: ANN001
        stmt = select(LocationAccess).order_by(LocationAccess.created_at.desc()).limit(limit)
        if observation_id is not None:
            stmt = stmt.where(LocationAccess.observation_id == observation_id)
        if user_id is not None:
            stmt = stmt.where(LocationAccess.user_id == user_id)
        return list(self.db.scalars(stmt))
