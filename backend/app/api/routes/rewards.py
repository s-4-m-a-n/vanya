from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.rewards import BadgeOut, LeaderboardOut, LevelOut, ProgressOut, StandingOut
from app.services.rewards import BADGES, level_for
from app.services.verification import build_xp_service
from app.services.xp import XpService

router = APIRouter(tags=["rewards"])


def get_xp_service(db: DbSession) -> XpService:
    return build_xp_service(db)


Service = Annotated[XpService, Depends(get_xp_service)]


@router.get("/me/progress")
def my_progress(user: CurrentUser, service: Service) -> ProgressOut:
    xp, pending = service.totals(user.id)
    stats = service.stats(user.id)
    level = level_for(xp)
    return ProgressOut(
        xp=xp,
        pending_xp=pending,
        level=LevelOut(number=level.number, floor=level.floor, next_at=level.next_at),
        reputation=user.reputation,
        verified=stats.verified,
        species=stats.species,
        rare_finds=stats.rare_finds,
        badges=[BadgeOut(code=b.badge, awarded_at=b.awarded_at) for b in service.badges(user.id)],
        all_badges=list(BADGES),
        rarity_bonus_left_today=service.rarity_bonus_left_today(user.id),
        leaderboard_visible=user.leaderboard_visible,
    )


@router.get("/leaderboards")
def leaderboard(
    user: CurrentUser,
    service: Service,
    period: Literal["week", "month", "all"] = "week",
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    # Large minimum: small circles could be used to pinpoint where someone earned XP.
    radius_km: Annotated[float, Query(ge=25, le=1000)] = 100,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LeaderboardOut:
    nearby = latitude is not None or longitude is not None
    if nearby and (latitude is None or longitude is None):
        raise HTTPException(422, detail="Send both latitude and longitude for a nearby board")
    entries, me = service.leaderboard(
        user,
        period,
        latitude,
        longitude,
        radius_km if nearby else None,
        limit,
    )
    return LeaderboardOut(
        period=period,
        scope="nearby" if nearby else "global",
        radius_km=radius_km if nearby else None,
        entries=[StandingOut(**vars(e)) for e in entries],
        me=StandingOut(**vars(me)) if me else None,
    )
