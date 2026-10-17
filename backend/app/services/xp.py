"""XP ledger, badges, progress and leaderboards (PRD F2)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from geoalchemy2 import Geography
from sqlalchemy import and_, cast, distinct, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    Observation,
    ObservationStatus,
    RestrictedArea,
    Taxon,
    User,
    UserBadge,
    XpEntry,
    XpKind,
    XpStatus,
)
from app.services.rarity import RarityService
from app.services.rewards import (
    DAILY_RARITY_CAP,
    REPEAT_RADIUS_M,
    REPEAT_WINDOW,
    AwardContext,
    UserStats,
    compute_award,
    earned_badges,
    level_for,
)

Period = Literal["week", "month", "all"]
ACTIVE = (ObservationStatus.SUBMITTED, ObservationStatus.IN_REVIEW, ObservationStatus.VERIFIED)
RARE_TIERS = ("rare", "very_rare", "exceptional")
THREATENED = ("CR", "EN", "VU")


def _point(lat: float, lon: float):  # noqa: ANN202
    return cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), Geography)


class XpService:
    def __init__(self, db: Session, rarity: RarityService) -> None:
        self.db = db
        self.rarity = rarity

    # --- Ledger --------------------------------------------------------------------------

    def sync(self, observation_id: uuid.UUID) -> None:
        """Bring the XP ledger in line with the observation's current status.

        Idempotent and order-independent, so duplicate or late events are harmless:
        in review -> provisional, verified -> confirmed, rejected -> void.
        """
        obs = self.db.scalar(
            select(Observation)
            .where(Observation.id == observation_id)
            .with_for_update(of=Observation)
        )
        if (
            obs is None
            or obs.taxon is None
            or obs.status
            not in (
                ObservationStatus.IN_REVIEW.value,
                ObservationStatus.VERIFIED.value,
                ObservationStatus.REJECTED.value,
            )
        ):
            self.db.rollback()
            return
        entries = {
            e.kind: e
            for e in self.db.scalars(select(XpEntry).where(XpEntry.observation_id == obs.id))
        }

        if obs.status == ObservationStatus.REJECTED.value:
            for entry in entries.values():
                entry.status, entry.confirmed_at = XpStatus.VOID.value, None
            self.db.commit()
            return

        verified = obs.status == ObservationStatus.VERIFIED.value
        live = [e for e in entries.values() if e.status != XpStatus.VOID.value]
        if verified and live and all(e.status == XpStatus.CONFIRMED.value for e in live):
            # Confirmed awards are final; later rarity recomputes don't change them.
            self._award_badges(obs)
            self.db.commit()
            return

        rarity = self.rarity.get(obs.taxon, obs.latitude, obs.longitude)
        award = compute_award(
            AwardContext(
                restricted=self._restricted(obs),
                repeat=self._is_repeat(obs),
                first_of_species=self._first_of_species(obs),
                rarity=rarity.score,
                rarity_bonus_used_today=self._rarity_used_today(obs),
                under_sampled=self.rarity.is_under_sampled(obs.taxon, obs.latitude, obs.longitude),
            )
        )
        obs.rarity_score, obs.rarity_tier = rarity.score, rarity.tier.value
        obs.reward_note = award.note

        status = XpStatus.CONFIRMED if verified else XpStatus.PROVISIONAL
        now = datetime.now(UTC) if verified else None
        for kind in XpKind:
            amount = award.amounts.get(kind, 0)
            entry = entries.get(kind.value)
            if amount and entry is None:
                self.db.add(
                    XpEntry(
                        user_id=obs.user_id,
                        observation_id=obs.id,
                        kind=kind.value,
                        amount=amount,
                        status=status.value,
                        confirmed_at=now,
                    )
                )
            elif amount:
                entry.amount, entry.status, entry.confirmed_at = amount, status.value, now
            elif entry is not None:
                entry.status, entry.confirmed_at = XpStatus.VOID.value, None
        if verified:
            self.db.flush()
            self._award_badges(obs)
        self.db.commit()

    def _restricted(self, obs: Observation) -> bool:
        return bool(
            self.db.scalar(
                select(
                    exists().where(
                        RestrictedArea.active.is_(True),
                        func.ST_Contains(RestrictedArea.area, obs.location),
                    )
                )
            )
        )

    def _others(self, obs: Observation):  # noqa: ANN202
        """Earlier, still-standing records of the same species by the same person."""
        return [
            Observation.user_id == obs.user_id,
            Observation.taxon_id == obs.taxon_id,
            Observation.id != obs.id,
            Observation.created_at < obs.created_at,
            Observation.status.in_([s.value for s in ACTIVE]),
        ]

    def _is_repeat(self, obs: Observation) -> bool:
        near = func.ST_DWithin(
            cast(Observation.location, Geography),
            _point(obs.latitude, obs.longitude),
            REPEAT_RADIUS_M,
        )
        window = Observation.observed_at.between(
            obs.observed_at - REPEAT_WINDOW, obs.observed_at + REPEAT_WINDOW
        )
        return bool(self.db.scalar(select(exists().where(*self._others(obs), near, window))))

    def _first_of_species(self, obs: Observation) -> bool:
        return not self.db.scalar(select(exists().where(*self._others(obs))))

    def _rarity_used_today(self, obs: Observation) -> int:
        day = obs.created_at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.scalar(
                select(func.coalesce(func.sum(XpEntry.amount), 0)).where(
                    XpEntry.user_id == obs.user_id,
                    XpEntry.kind == XpKind.RARITY.value,
                    XpEntry.status != XpStatus.VOID.value,
                    XpEntry.observation_id != obs.id,
                    XpEntry.created_at >= day,
                    XpEntry.created_at < day + timedelta(days=1),
                )
            )
            or 0
        )

    # --- Badges --------------------------------------------------------------------------

    def stats(self, user_id: uuid.UUID) -> UserStats:
        verified = and_(
            Observation.user_id == user_id,
            Observation.status == ObservationStatus.VERIFIED.value,
        )
        row = self.db.execute(
            select(
                func.count(Observation.id),
                func.count(distinct(Observation.taxon_id)),
                func.count(distinct(Observation.taxon_id)).filter(Taxon.taxon_group == "bird"),
                func.count(distinct(Observation.taxon_id)).filter(Taxon.taxon_group == "animal"),
                func.count(distinct(Observation.taxon_id)).filter(Taxon.taxon_group == "plant"),
                func.count(Observation.id).filter(Observation.rarity_tier.in_(RARE_TIERS)),
                func.count(Observation.id).filter(Taxon.iucn_status.in_(THREATENED)),
                func.count(distinct(func.date(Observation.observed_at))),
            )
            .select_from(Observation)
            .join(Taxon, Observation.taxon_id == Taxon.id)
            .where(verified)
        ).one()
        explorer = self.db.scalar(
            select(func.count()).where(
                XpEntry.user_id == user_id,
                XpEntry.kind == XpKind.EXPLORER.value,
                XpEntry.status == XpStatus.CONFIRMED.value,
            )
        )
        reputation = self.db.scalar(select(User.reputation).where(User.id == user_id)) or 0
        return UserStats(
            *row[:7], explorer=explorer or 0, active_days=row[7], reputation=reputation
        )

    def _award_badges(self, obs: Observation) -> None:
        for code in earned_badges(self.stats(obs.user_id)):
            self.db.execute(
                insert(UserBadge)
                .values(user_id=obs.user_id, badge=code, observation_id=obs.id)
                .on_conflict_do_nothing()
            )

    # --- Progress and leaderboards -------------------------------------------------------

    def totals(self, user_id: uuid.UUID) -> tuple[int, int]:
        """(confirmed XP, provisional XP)."""
        rows = dict(
            self.db.execute(
                select(XpEntry.status, func.sum(XpEntry.amount))
                .where(XpEntry.user_id == user_id)
                .group_by(XpEntry.status)
            ).all()
        )
        return rows.get(XpStatus.CONFIRMED.value, 0) or 0, rows.get(
            XpStatus.PROVISIONAL.value, 0
        ) or 0

    def rarity_bonus_left_today(self, user_id: uuid.UUID) -> int:
        day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        used = self.db.scalar(
            select(func.coalesce(func.sum(XpEntry.amount), 0)).where(
                XpEntry.user_id == user_id,
                XpEntry.kind == XpKind.RARITY.value,
                XpEntry.status != XpStatus.VOID.value,
                XpEntry.created_at >= day,
            )
        )
        return max(0, DAILY_RARITY_CAP - (used or 0))

    def badges(self, user_id: uuid.UUID) -> list[UserBadge]:
        return list(
            self.db.scalars(
                select(UserBadge).where(UserBadge.user_id == user_id).order_by(UserBadge.awarded_at)
            )
        )

    def leaderboard(
        self,
        viewer: User,
        period: Period = "all",
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        limit: int = 20,
    ) -> tuple[list["Standing"], "Standing | None"]:
        conditions = [XpEntry.status == XpStatus.CONFIRMED.value]
        start = period_start(period)
        if start is not None:
            conditions.append(XpEntry.confirmed_at >= start)
        stmt = select(XpEntry.user_id, func.sum(XpEntry.amount).label("xp"))
        if latitude is not None and longitude is not None and radius_km:
            stmt = stmt.join(Observation, Observation.id == XpEntry.observation_id)
            # Generalised points only: filtering on exact ones would let someone
            # triangulate a sensitive sighting by shrinking the radius.
            conditions.append(
                func.ST_DWithin(
                    cast(Observation.generalised_location, Geography),
                    _point(latitude, longitude),
                    radius_km * 1000,
                )
            )
        sums = stmt.where(*conditions).group_by(XpEntry.user_id).subquery()
        ranked = (
            select(
                User.id,
                User.display_name,
                sums.c.xp,
                func.rank().over(order_by=sums.c.xp.desc()).label("rank"),
            )
            .join(sums, sums.c.user_id == User.id)
            .where(User.leaderboard_visible.is_(True), User.deleted_at.is_(None))
            .subquery()
        )
        top = self.db.execute(
            select(ranked).order_by(ranked.c.rank, ranked.c.display_name).limit(limit)
        ).all()
        mine = self.db.execute(select(ranked).where(ranked.c.id == viewer.id)).first()

        levels = self._levels([row.id for row in top] + ([mine.id] if mine else []))

        def standing(row) -> Standing:  # noqa: ANN001
            return Standing(
                rank=row.rank,
                user_id=row.id,
                display_name=row.display_name,
                xp=row.xp,
                level=levels.get(row.id, 1),
                is_me=row.id == viewer.id,
            )

        return [standing(r) for r in top], standing(mine) if mine else None

    def _levels(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not user_ids:
            return {}
        rows = self.db.execute(
            select(XpEntry.user_id, func.sum(XpEntry.amount))
            .where(XpEntry.user_id.in_(user_ids), XpEntry.status == XpStatus.CONFIRMED.value)
            .group_by(XpEntry.user_id)
        ).all()
        return {uid: level_for(xp or 0).number for uid, xp in rows}


@dataclass(frozen=True)
class Standing:
    rank: int
    user_id: uuid.UUID
    display_name: str
    xp: int
    level: int
    is_me: bool


def period_start(period: Period, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return midnight - timedelta(days=midnight.weekday())  # Monday
    if period == "month":
        return midnight.replace(day=1)
    return None
