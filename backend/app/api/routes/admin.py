import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import DbSession, require_roles
from app.models import IucnStatus, Role, StatusSource, Taxon, User
from app.schemas.auth import RoleUpdate, UserOut
from app.schemas.taxa import TaxonOut
from app.services.location_access import LocationAccessService
from app.services.taxon_status import refresh_taxon_observations

require_admin = require_roles(Role.ADMIN)
AdminUser = Annotated[User, Depends(require_admin)]

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.patch("/users/{user_id}/role")
def set_role(user_id: uuid.UUID, body: RoleUpdate, db: DbSession, admin: AdminUser) -> UserOut:
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(404, detail="User not found")
    if user.id == admin.id and body.role != Role.ADMIN:
        raise HTTPException(400, detail="Admins cannot demote themselves")
    user.role = body.role.value
    db.commit()
    return UserOut.model_validate(user)


class LocationAccessOut(BaseModel):
    id: int
    user_id: uuid.UUID
    user_email: str
    user_role: str
    observation_id: uuid.UUID
    taxon_id: int | None
    geoprivacy: str | None
    purpose: str
    note: str | None
    ip: str | None
    created_at: datetime


@router.get("/location-access")
def location_access_log(
    db: DbSession,
    observation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[LocationAccessOut]:
    """Who saw which exact locations, when and why (PRD F5)."""
    rows = LocationAccessService(db).log(observation_id, user_id, limit)
    return [LocationAccessOut.model_validate(r, from_attributes=True) for r in rows]


class TaxonProtectionUpdate(BaseModel):
    # null clears the override, so sensitivity follows the conservation status again.
    sensitivity: Literal["open", "obscured", "hidden"] | None = None
    iucn_status: IucnStatus | None = Field(default=None, description="Manual status override")


class TaxonProtectionOut(BaseModel):
    taxon: TaxonOut
    sensitivity: str | None
    status_source: str | None
    observations_updated: int


@router.patch("/taxa/{taxon_id}")
def set_taxon_protection(
    taxon_id: int, body: TaxonProtectionUpdate, db: DbSession, admin: AdminUser
) -> TaxonProtectionOut:
    taxon = db.get(Taxon, taxon_id)
    if taxon is None:
        raise HTTPException(404, detail="Taxon not found")
    if "sensitivity" in body.model_fields_set:
        taxon.sensitivity = body.sensitivity
    if body.iucn_status is not None:
        taxon.iucn_status = body.iucn_status.value
        taxon.status_source = StatusSource.MANUAL.value
        taxon.status_checked_at = datetime.now(UTC)
    updated = refresh_taxon_observations(db, taxon)
    db.commit()
    return TaxonProtectionOut(
        taxon=TaxonOut.build(taxon, admin.locale),
        sensitivity=taxon.sensitivity,
        status_source=taxon.status_source,
        observations_updated=updated,
    )
