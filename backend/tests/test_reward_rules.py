import pytest

from app.models import RarityTier, XpKind
from app.services.rarity import combine, endemism_score, frequency_score, tier_for
from app.services.rewards import (
    AwardContext,
    UserStats,
    compute_award,
    earned_badges,
    level_for,
    rarity_bonus,
)


def test_ordinary_sighting_earns_base_xp():
    award = compute_award(AwardContext())
    assert award.amounts == {XpKind.BASE: 10} and award.note is None


def test_bonuses_stack():
    award = compute_award(AwardContext(rarity=0.8, first_of_species=True, under_sampled=True))
    assert award.amounts == {
        XpKind.BASE: 10,
        XpKind.RARITY: 64,
        XpKind.FIRST_SPECIES: 20,
        XpKind.EXPLORER: 15,
    }
    assert award.total == 109


def test_repeat_sighting_earns_almost_nothing():
    award = compute_award(AwardContext(repeat=True, rarity=1.0, first_of_species=True))
    assert award.amounts == {XpKind.BASE: 2} and award.note == "repeat"


def test_restricted_area_earns_nothing():
    award = compute_award(AwardContext(restricted=True, rarity=1.0))
    assert award.total == 0 and award.note == "restricted_area"


@pytest.mark.parametrize(
    ("used", "expected_bonus", "note"),
    [(0, 100, None), (150, 50, "daily_cap"), (200, 0, "daily_cap"), (500, 0, "daily_cap")],
)
def test_rare_bonus_is_capped_per_day(used, expected_bonus, note):
    award = compute_award(AwardContext(rarity=1.0, rarity_bonus_used_today=used))
    assert award.amounts.get(XpKind.RARITY, 0) == expected_bonus
    assert award.note == note


@pytest.mark.parametrize(
    ("rarity", "bonus"), [(None, 0), (0.0, 0), (0.2, 4), (0.5, 25), (0.8, 64), (1.0, 100)]
)
def test_rarity_bonus_is_convex(rarity, bonus):
    assert rarity_bonus(rarity) == bonus


@pytest.mark.parametrize(
    ("xp", "level", "floor", "next_at"),
    [
        (0, 1, 0, 50),
        (49, 1, 0, 50),
        (50, 2, 50, 150),
        (2999, 9, 2300, 3000),
        (3000, 10, 3000, 4000),
        (5500, 12, 5000, 6000),
        (-5, 1, 0, 50),
    ],
)
def test_levels(xp, level, floor, next_at):
    result = level_for(xp)
    assert (result.number, result.floor, result.next_at) == (level, floor, next_at)


def test_badges():
    assert earned_badges(UserStats()) == set()
    stats = UserStats(verified=12, species=11, birds=10, rare_finds=1, active_days=7, reputation=50)
    assert earned_badges(stats) == {
        "first_sighting",
        "species_10",
        "birds_10",
        "rare_find",
        "days_7",
        "trusted_observer",
    }


# Real GBIF figures (Oct 2026); `top` is the group's most-recorded species in the cell.
@pytest.mark.parametrize(
    ("local", "top", "group", "expected"),
    [
        (16_129, 24_784, 691_302, 0.0),  # house sparrow, Kathmandu (top: house crow)
        (144_909, 270_773, 7_017_055, 0.0),  # rock dove, London (top: wood pigeon)
        (1_023, 34_165, 4_595_816, pytest.approx(0.17, abs=0.01)),  # dandelion, London
        (194, 33_500, 1_046_908, pytest.approx(0.41, abs=0.01)),  # bank myna, Kolkata
        (0, 270_773, 7_017_054, 1.0),  # house crow, London: a vagrant
        (5, 8, 20, None),  # too few group records to judge
    ],
)
def test_frequency_score(local, top, group, expected):
    assert frequency_score(local, top, group) == expected


@pytest.mark.parametrize(
    ("countries", "score"),
    [(None, None), (0, None), (1, 1.0), (2, 0.75), (4, 0.5), (8, 0.25), (90, 0.0)],
)
def test_endemism_score(countries, score):
    assert endemism_score(countries) == score


def test_status_can_dominate_and_regional_rarity_blends():
    assert combine(status=0.8, frequency=0.0, endemism=0.0) == 0.8  # common tiger is still EN
    assert combine(status=0.0, frequency=1.0, endemism=1.0) == 1.0
    assert combine(status=0.0, frequency=0.4, endemism=None) == 0.3
    assert combine(status=0.0, frequency=None, endemism=None) == 0.0


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (0.0, RarityTier.COMMON),
        (0.19, RarityTier.COMMON),
        (0.2, RarityTier.UNCOMMON),
        (0.45, RarityTier.RARE),
        (0.6, RarityTier.VERY_RARE),
        (0.95, RarityTier.EXCEPTIONAL),
    ],
)
def test_tiers(score, tier):
    assert tier_for(score) == tier
