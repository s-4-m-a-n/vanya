import uuid
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.adapters.events import EventBus, get_event_bus
from app.adapters.species_id import SpeciesIdentifier, get_species_identifier
from app.adapters.storage import BlobStorage, get_storage
from app.api.deps import CurrentUser, DbSession, service_error
from app.config import get_settings
from app.models import Habitat, Observation, ObservationStatus, Role, User
from app.schemas.observations import (
    REVIEW_ROLES,
    ExactLocationOut,
    ExactLocationRequest,
    ObservationOut,
    ObservationPage,
    SpeciesChoice,
)
from app.services.gbif import get_gbif_client
from app.services.location_access import LocationAccessService
from app.services.observations import NewObservation, ObservationError, ObservationService
from app.services.photos import InvalidPhoto
from app.services.taxonomy import TaxonResolver

router = APIRouter(prefix="/observations", tags=["observations"])

Storage = Annotated[BlobStorage, Depends(get_storage)]


def get_observation_service(
    db: DbSession,
    storage: Storage,
    identifier: Annotated[SpeciesIdentifier, Depends(get_species_identifier)],
    events: Annotated[EventBus, Depends(get_event_bus)],
) -> ObservationService:
    settings = get_settings()
    gbif = get_gbif_client() if settings.taxonomy_resolver == "gbif" else None
    return ObservationService(db, storage, identifier, TaxonResolver(db, gbif), events, settings)


Service = Annotated[ObservationService, Depends(get_observation_service)]


def _load(db: DbSession, observation_id: uuid.UUID, user: User, write: bool = False) -> Observation:
    observation = db.get(Observation, observation_id)
    allowed = observation is not None and (
        observation.user_id == user.id
        or user.role == Role.ADMIN.value
        or (not write and user.role in REVIEW_ROLES)
    )
    if not allowed:
        raise HTTPException(404, detail="Observation not found")
    return observation  # type: ignore[return-value]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_observation(
    request: Request,
    response: Response,
    user: CurrentUser,
    service: Service,
    storage: Storage,
    photo: Annotated[UploadFile, File()],
    client_uuid: Annotated[uuid.UUID, Form()],
    latitude: Annotated[float | None, Form(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
    observed_at: Annotated[datetime | None, Form()] = None,
    habitat: Annotated[Habitat | None, Form()] = None,
    notes: Annotated[str | None, Form(max_length=1000)] = None,
    taxon_id: Annotated[int | None, Form()] = None,
    utc_offset_minutes: Annotated[int | None, Form(ge=-14 * 60, le=14 * 60)] = None,
    # Never show this record's exact location publicly, whatever the species.
    obscure_location: Annotated[bool, Form()] = False,
) -> ObservationOut:
    limit = get_settings().max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit + 64 * 1024:
        raise HTTPException(413, detail="Photo too large")
    data = photo.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, detail="Photo too large")

    try:
        observation, created = service.create(
            user,
            data,
            NewObservation(
                client_uuid=client_uuid,
                latitude=latitude,
                longitude=longitude,
                accuracy_m=accuracy_m,
                observed_at=observed_at,
                habitat=habitat,
                notes=notes,
                taxon_id=taxon_id,
                utc_offset_minutes=utc_offset_minutes,
                obscure_location=obscure_location,
            ),
        )
    except ObservationError as exc:
        raise service_error(exc) from exc
    except InvalidPhoto as exc:
        raise HTTPException(422, detail={"code": "invalid_photo", "message": str(exc)}) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return ObservationOut.build(observation, user, storage)


@router.get("/mine")
def list_my_observations(
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
    status_filter: Annotated[ObservationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ObservationPage:
    stmt = select(Observation).where(Observation.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Observation.status == status_filter.value)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Observation.observed_at.desc()).limit(limit).offset(offset)
    ).all()
    return ObservationPage(
        items=[ObservationOut.build(o, user, storage) for o in rows], total=total
    )


@router.get("/{observation_id}")
def get_observation(
    observation_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: Storage
) -> ObservationOut:
    observation = _load(db, observation_id, user)
    return ObservationOut.build(observation, user, storage)


@router.put("/{observation_id}/species")
def choose_species(
    observation_id: uuid.UUID,
    body: SpeciesChoice,
    user: CurrentUser,
    db: DbSession,
    service: Service,
    storage: Storage,
) -> ObservationOut:
    observation = _load(db, observation_id, user, write=True)
    try:
        observation = service.choose_species(observation, body.taxon_id)
    except ObservationError as exc:
        raise service_error(exc) from exc
    return ObservationOut.build(observation, user, storage)


@router.delete("/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(
    observation_id: uuid.UUID, user: CurrentUser, db: DbSession, service: Service
) -> None:
    service.delete(_load(db, observation_id, user, write=True))


@router.post("/{observation_id}/exact-location")
def exact_location(
    observation_id: uuid.UUID,
    body: ExactLocationRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> ExactLocationOut:
    """True coordinates of a protected record. Role-gated and audit-logged (PRD F5)."""
    observation = _load(db, observation_id, user)
    try:
        exact = LocationAccessService(db).reveal(
            observation,
            user,
            body.purpose,
            body.note,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ObservationError as exc:
        raise service_error(exc) from exc
    return ExactLocationOut(**vars(exact))
