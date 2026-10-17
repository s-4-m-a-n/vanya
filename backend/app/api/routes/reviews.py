import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.adapters.storage import BlobStorage, get_storage
from app.api.deps import DbSession, require_roles, service_error
from app.models import Role, TaxonGroup, User
from app.schemas.observations import ObservationOut, QueueItem, QueuePage, ReviewIn
from app.services.observations import ObservationError
from app.services.verification import VerificationService, build_verification_service

router = APIRouter(prefix="/reviews", tags=["reviews"])

Reviewer = Annotated[User, Depends(require_roles(Role.VERIFIER))]
Storage = Annotated[BlobStorage, Depends(get_storage)]


def get_verification_service(db: DbSession) -> VerificationService:
    return build_verification_service(db)


Service = Annotated[VerificationService, Depends(get_verification_service)]


@router.get("/queue")
def review_queue(
    reviewer: Reviewer,
    service: Service,
    storage: Storage,
    group: TaxonGroup | None = None,
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float | None, Query(ge=25, le=20_000)] = None,
    # Ignore the reviewer's saved review area.
    anywhere: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueuePage:
    rows, total = service.queue(
        reviewer,
        group=group.value if group else None,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        anywhere=anywhere,
        limit=limit,
        offset=offset,
    )
    return QueuePage(items=[QueueItem.build_item(o, reviewer, storage) for o in rows], total=total)


@router.post("/{observation_id}")
def submit_review(
    observation_id: uuid.UUID,
    body: ReviewIn,
    reviewer: Reviewer,
    service: Service,
    storage: Storage,
) -> ObservationOut:
    try:
        observation = service.review(
            observation_id, reviewer, body.decision, body.taxon_id, body.reason, body.comment
        )
    except ObservationError as exc:
        raise service_error(exc) from exc
    return ObservationOut.build(observation, reviewer, storage)
