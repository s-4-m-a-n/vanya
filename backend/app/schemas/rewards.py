import uuid
from datetime import datetime

from pydantic import BaseModel


class LevelOut(BaseModel):
    number: int
    floor: int
    next_at: int


class BadgeOut(BaseModel):
    code: str
    awarded_at: datetime


class ProgressOut(BaseModel):
    xp: int  # confirmed
    pending_xp: int  # provisional, waiting on verification
    level: LevelOut
    reputation: int
    verified: int
    species: int
    rare_finds: int
    badges: list[BadgeOut]
    all_badges: list[str]
    rarity_bonus_left_today: int
    leaderboard_visible: bool


class StandingOut(BaseModel):
    rank: int
    user_id: uuid.UUID
    display_name: str
    xp: int
    level: int
    is_me: bool


class LeaderboardOut(BaseModel):
    period: str
    scope: str
    radius_km: float | None
    entries: list[StandingOut]
    me: StandingOut | None
