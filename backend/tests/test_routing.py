import pytest

from app.models import ObservationStatus, RejectReason, VerificationMethod
from app.services.commonness import Commonness
from app.services.integrity import Flag
from app.services.routing import RoutingInput, decide_route, tally

SPARROW = 1


def route(**overrides):
    data = {
        "chosen_taxon_id": SPARROW,
        "iucn_status": "LC",
        "candidates": [(SPARROW, 0.95), (2, 0.03)],
        "id_is_organism": True,
        "commonness": Commonness.COMMON,
        "flags": [],
    }
    data.update(overrides)
    return decide_route(RoutingInput(**data), min_confidence=0.85)


def test_confident_common_clean_record_is_auto_accepted():
    result = route()
    assert result.status == ObservationStatus.VERIFIED
    assert result.method == VerificationMethod.AUTO
    assert result.reasons == [] and result.required_reviews is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"candidates": [(SPARROW, 0.6)]}, "low_confidence"),
        ({"candidates": [(2, 0.9), (SPARROW, 0.05)]}, "differs_from_ai"),
        ({"candidates": []}, "no_ai_identification"),
        ({"id_is_organism": False}, "ai_saw_no_organism"),
        ({"commonness": Commonness.UNKNOWN}, "commonness_unknown"),
        ({"flags": [Flag("location_mismatch", "hard")]}, "location_mismatch"),
    ],
)
def test_ordinary_doubts_need_one_reviewer(overrides, reason):
    result = route(**overrides)
    assert result.status == ObservationStatus.IN_REVIEW
    assert reason in result.reasons
    assert result.required_reviews == 1 and not result.is_rare


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"iucn_status": "EN"}, "threatened_species"),
        ({"iucn_status": "EW"}, "extinct_claim"),
        ({"commonness": Commonness.UNCOMMON}, "locally_uncommon"),
        ({"commonness": Commonness.OUT_OF_RANGE}, "out_of_range"),
    ],
)
def test_rare_claims_need_two_reviewers_even_when_confident(overrides, reason):
    result = route(**overrides)
    assert result.status == ObservationStatus.IN_REVIEW
    assert reason in result.reasons
    assert result.is_rare and result.required_reviews == 2


def test_soft_flags_do_not_block_auto_accept():
    assert route(flags=[Flag("no_camera_metadata", "soft")]).status == ObservationStatus.VERIFIED


def test_resubmitting_own_photo_is_rejected_automatically():
    result = route(flags=[Flag("duplicate_own", "hard")])
    assert result.status == ObservationStatus.REJECTED
    assert result.rejection_reason == RejectReason.DUPLICATE_OR_STOCK


def test_reasons_accumulate():
    result = route(
        iucn_status="CR", candidates=[(SPARROW, 0.3)], flags=[Flag("screenshot", "hard")]
    )
    assert result.reasons == ["threatened_species", "low_confidence", "screenshot"]


CONFIRM, REJECT = "confirm", "reject"


@pytest.mark.parametrize(
    ("reviews", "required", "expected"),
    [
        ([], 1, ObservationStatus.IN_REVIEW),
        ([(CONFIRM, 1, None)], 1, ObservationStatus.VERIFIED),
        ([(REJECT, None, "not_wild")], 1, ObservationStatus.REJECTED),
        ([(CONFIRM, 1, None)], 2, ObservationStatus.IN_REVIEW),
        ([(CONFIRM, 1, None), (CONFIRM, 1, None)], 2, ObservationStatus.VERIFIED),
        # Experts disagree on the species: wait for a third opinion.
        ([(CONFIRM, 1, None), (CONFIRM, 2, None)], 2, ObservationStatus.IN_REVIEW),
        (
            [(CONFIRM, 1, None), (CONFIRM, 2, None), (CONFIRM, 1, None)],
            2,
            ObservationStatus.VERIFIED,
        ),
        # Split confirm/reject: no outcome until one side leads with enough votes.
        ([(CONFIRM, 1, None), (REJECT, None, "not_wild")], 1, ObservationStatus.IN_REVIEW),
        (
            [(CONFIRM, 1, None), (REJECT, None, "not_wild"), (REJECT, None, "unidentifiable")],
            2,
            ObservationStatus.REJECTED,
        ),
    ],
)
def test_tally(reviews, required, expected):
    assert tally(reviews, required).status == expected


def test_tally_reports_agreed_species_and_main_reject_reason():
    assert tally([(CONFIRM, 7, None), (CONFIRM, 7, None)], 2).taxon_id == 7
    rejected = tally(
        [(REJECT, None, "not_wild"), (REJECT, None, "not_wild"), (REJECT, None, "inappropriate")], 2
    )
    assert rejected.rejection_reason == RejectReason.NOT_WILD
