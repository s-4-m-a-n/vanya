"""Reward rules (PRD F2, §6) as pure functions: XP awards, levels and badges.

Rewards must not corrupt data, so:
- every award stays provisional until the record is verified (void if rejected);
- repeat sightings of a species at the same place and time earn almost nothing;
- the rare-find bonus is capped per day;
- records inside restricted areas earn nothing (the record itself is still kept).
"""

from dataclasses import dataclass, field
from datetime import timedelta

from app.models import XpKind

XP_BASE = 10
XP_REPEAT = 2
XP_FIRST_SPECIES = 20
XP_EXPLORER = 15
RARITY_BONUS_MAX = 100
DAILY_RARITY_CAP = 200

REPEAT_RADIUS_M = 1000
REPEAT_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class AwardContext:
    restricted: bool = False
    repeat: bool = False
    first_of_species: bool = False
    rarity: float | None = None
    rarity_bonus_used_today: int = 0
    under_sampled: bool = False


@dataclass(frozen=True)
class Award:
    amounts: dict[XpKind, int] = field(default_factory=dict)
    # "restricted_area", "repeat" or "daily_cap": why XP was withheld or reduced.
    note: str | None = None

    @property
    def total(self) -> int:
        return sum(self.amounts.values())


def rarity_bonus(rarity: float | None) -> int:
    """Convex in rarity: common finds earn little, exceptional ones up to 100."""
    return round(RARITY_BONUS_MAX * (rarity or 0.0) ** 2)


def compute_award(ctx: AwardContext) -> Award:
    if ctx.restricted:
        return Award(note="restricted_area")
    if ctx.repeat:
        return Award({XpKind.BASE: XP_REPEAT}, note="repeat")

    amounts = {XpKind.BASE: XP_BASE}
    note = None
    bonus = rarity_bonus(ctx.rarity)
    remaining = max(0, DAILY_RARITY_CAP - ctx.rarity_bonus_used_today)
    if bonus > remaining:
        bonus, note = remaining, "daily_cap"
    if bonus:
        amounts[XpKind.RARITY] = bonus
    if ctx.first_of_species:
        amounts[XpKind.FIRST_SPECIES] = XP_FIRST_SPECIES
    if ctx.under_sampled:
        amounts[XpKind.EXPLORER] = XP_EXPLORER
    return Award(amounts, note)


# --- Levels ----------------------------------------------------------------------------

# XP needed for levels 1-10; after that, every 1000 XP is another level.
LEVEL_THRESHOLDS = [0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000]


@dataclass(frozen=True)
class Level:
    number: int
    floor: int  # XP at which this level started
    next_at: int  # XP needed for the next level


def level_for(xp: int) -> Level:
    xp = max(xp, 0)
    if xp >= LEVEL_THRESHOLDS[-1]:
        extra = (xp - LEVEL_THRESHOLDS[-1]) // 1000
        floor = LEVEL_THRESHOLDS[-1] + extra * 1000
        return Level(len(LEVEL_THRESHOLDS) + extra, floor, floor + 1000)
    number = max(i for i, threshold in enumerate(LEVEL_THRESHOLDS, start=1) if xp >= threshold)
    return Level(number, LEVEL_THRESHOLDS[number - 1], LEVEL_THRESHOLDS[number])


# --- Badges ----------------------------------------------------------------------------


@dataclass(frozen=True)
class UserStats:
    verified: int = 0
    species: int = 0
    birds: int = 0
    animals: int = 0
    plants: int = 0
    rare_finds: int = 0
    threatened: int = 0
    explorer: int = 0
    active_days: int = 0
    reputation: int = 0


BADGES: dict[str, tuple[str, int]] = {
    # code: (UserStats field, threshold)
    "first_sighting": ("verified", 1),
    "species_10": ("species", 10),
    "species_50": ("species", 50),
    "species_100": ("species", 100),
    "birds_10": ("birds", 10),
    "animals_10": ("animals", 10),
    "plants_10": ("plants", 10),
    "rare_find": ("rare_finds", 1),
    "threatened_witness": ("threatened", 1),
    "explorer_5": ("explorer", 5),
    "days_7": ("active_days", 7),
    "trusted_observer": ("reputation", 50),
}


def earned_badges(stats: UserStats) -> set[str]:
    return {code for code, (attr, need) in BADGES.items() if getattr(stats, attr) >= need}
