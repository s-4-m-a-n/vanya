"""Verification routing and expert consensus (PRD F3, §6) as pure functions."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.models import ObservationStatus, RejectReason, ReviewDecision, VerificationMethod
from app.services.commonness import Commonness
from app.services.integrity import Flag

THREATENED = {"CR", "EN", "VU"}
EXTINCT = {"EX", "EW"}


@dataclass(frozen=True)
class RoutingInput:
    chosen_taxon_id: int
    iucn_status: str
    # (taxon_id, confidence) in the identifier's rank order
    candidates: Sequence[tuple[int | None, float]]
    id_is_organism: bool | None
    commonness: Commonness
    flags: Sequence[Flag]


@dataclass(frozen=True)
class Route:
    status: ObservationStatus
    is_rare: bool
    required_reviews: int | None
    reasons: list[str] = field(default_factory=list)
    method: VerificationMethod | None = None
    rejection_reason: RejectReason | None = None


def is_rare_status(iucn_status: str) -> bool:
    return iucn_status in THREATENED | EXTINCT


def decide_route(data: RoutingInput, min_confidence: float) -> Route:
    """Auto-accept confident, common, clean records; everything else goes to experts.

    Rare claims (threatened, locally uncommon, out of range) need two reviewers.
    """
    hard = sorted({f.code for f in data.flags if f.severity == "hard"})
    if "duplicate_own" in hard:
        # The observer already submitted this exact photo.
        return Route(
            ObservationStatus.REJECTED,
            is_rare=False,
            required_reviews=None,
            reasons=["duplicate_own"],
            method=VerificationMethod.AUTO,
            rejection_reason=RejectReason.DUPLICATE_OR_STOCK,
        )

    reasons: list[str] = []
    rare = False
    if data.iucn_status in EXTINCT:
        reasons.append("extinct_claim")
        rare = True
    elif data.iucn_status in THREATENED:
        reasons.append("threatened_species")
        rare = True

    if data.commonness == Commonness.OUT_OF_RANGE:
        reasons.append("out_of_range")
        rare = True
    elif data.commonness == Commonness.UNCOMMON:
        reasons.append("locally_uncommon")
        rare = True
    elif data.commonness == Commonness.UNKNOWN:
        reasons.append("commonness_unknown")

    if not data.candidates:
        reasons.append("no_ai_identification")
    elif data.candidates[0][0] != data.chosen_taxon_id:
        reasons.append("differs_from_ai")
    elif data.candidates[0][1] < min_confidence:
        reasons.append("low_confidence")
    if data.id_is_organism is False:
        reasons.append("ai_saw_no_organism")
    reasons.extend(hard)

    if not reasons:
        return Route(
            ObservationStatus.VERIFIED,
            is_rare=False,
            required_reviews=None,
            method=VerificationMethod.AUTO,
        )
    return Route(ObservationStatus.IN_REVIEW, rare, 2 if rare else 1, reasons)


@dataclass(frozen=True)
class Tally:
    status: ObservationStatus
    taxon_id: int | None = None
    rejection_reason: RejectReason | None = None


def tally(reviews: Sequence[tuple[str, int | None, str | None]], required: int) -> Tally:
    """Decide once one outcome has `required` votes and leads all others.

    Confirmations only count together when they agree on the species, so two experts
    naming different species leave the record open for a third opinion.
    """
    confirms = Counter(t for d, t, _ in reviews if d == ReviewDecision.CONFIRM.value)
    reject_reasons = [r for d, _, r in reviews if d == ReviewDecision.REJECT.value]
    taxon_id, top = confirms.most_common(1)[0] if confirms else (None, 0)

    if top >= required and top > len(reject_reasons):
        return Tally(ObservationStatus.VERIFIED, taxon_id=taxon_id)
    if len(reject_reasons) >= required and len(reject_reasons) > top:
        reason = Counter(reject_reasons).most_common(1)[0][0]
        return Tally(ObservationStatus.REJECTED, rejection_reason=RejectReason(reason))
    return Tally(ObservationStatus.IN_REVIEW)
