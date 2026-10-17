"""Organisation workspaces and the dashboard (PRD F4). Members only; others get 404."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.adapters.storage import BlobStorage, get_storage
from app.api.deps import CurrentUser, DbSession, require_roles, service_error
from app.models import MemberRole, OrganisationKind, Role, TaxonGroup, User, WatchCategory
from app.schemas.taxa import TaxonOut
from app.services.dashboard import GRID_STEPS, DashboardService, Scope
from app.services.geometry import InvalidGeometry
from app.services.observations import ObservationError
from app.services.organisations import OrganisationService

router = APIRouter(prefix="/orgs", tags=["organisations"])


def get_org_service(db: DbSession) -> OrganisationService:
    return OrganisationService(db)


Orgs = Annotated[OrganisationService, Depends(get_org_service)]
Storage = Annotated[BlobStorage, Depends(get_storage)]


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    kind: OrganisationKind


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: OrganisationKind
    my_role: MemberRole


class MemberIn(BaseModel):
    email: str
    role: MemberRole = MemberRole.ANALYST


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str
    role: MemberRole


class RegionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geometry: dict[str, Any] = Field(
        description="GeoJSON Polygon, MultiPolygon or Feature(Collection)"
    )


class WatchIn(BaseModel):
    category: WatchCategory
    note: str | None = Field(default=None, max_length=500)


class WatchOut(BaseModel):
    taxon: TaxonOut
    category: WatchCategory
    note: str | None


def _call(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    try:
        return fn(*args, **kwargs)
    except ObservationError as exc:
        raise service_error(exc) from exc


@router.get("")
def my_organisations(user: CurrentUser, orgs: Orgs) -> list[OrgOut]:
    return [
        OrgOut(id=o.id, name=o.name, kind=OrganisationKind(o.kind), my_role=MemberRole(role))
        for o, role in orgs.mine(user)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_organisation(
    body: OrgIn,
    orgs: Orgs,
    admin: Annotated[User, Depends(require_roles(Role.ADMIN))],
) -> OrgOut:
    org = _call(orgs.create, body.name, body.kind.value, creator=admin)
    return OrgOut(id=org.id, name=org.name, kind=body.kind, my_role=MemberRole.ADMIN)


# --- Members ---------------------------------------------------------------------------


@router.get("/{org_id}/members")
def members(org_id: uuid.UUID, user: CurrentUser, orgs: Orgs) -> list[MemberOut]:
    org = _call(orgs.get_for, org_id, user)
    return [
        MemberOut(
            user_id=m.user_id,
            display_name=m.user.display_name,
            email=m.user.email,
            role=MemberRole(m.role),
        )
        for m in sorted(org.members, key=lambda m: m.user.display_name)
    ]


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(org_id: uuid.UUID, body: MemberIn, user: CurrentUser, orgs: Orgs) -> MemberOut:
    org = _call(orgs.get_for, org_id, user, admin=True)
    member = _call(orgs.add_member, org, body.email, body.role)
    return MemberOut(
        user_id=member.user_id,
        display_name=member.user.display_name,
        email=member.user.email,
        role=MemberRole(member.role),
    )


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(org_id: uuid.UUID, user_id: uuid.UUID, user: CurrentUser, orgs: Orgs) -> None:
    org = _call(orgs.get_for, org_id, user, admin=True)
    _call(orgs.remove_member, org, user_id)


# --- Regions ---------------------------------------------------------------------------


@router.get("/{org_id}/regions")
def regions(org_id: uuid.UUID, user: CurrentUser, orgs: Orgs) -> list[dict[str, Any]]:
    return orgs.region_geojson(_call(orgs.get_for, org_id, user))


@router.post("/{org_id}/regions", status_code=status.HTTP_201_CREATED)
def add_region(org_id: uuid.UUID, body: RegionIn, user: CurrentUser, orgs: Orgs) -> dict[str, Any]:
    org = _call(orgs.get_for, org_id, user, admin=True)
    try:
        region = orgs.add_region(org, body.name, body.geometry)
    except InvalidGeometry as exc:
        raise HTTPException(422, detail={"code": "invalid_geometry", "message": str(exc)}) from exc
    return next(r for r in orgs.region_geojson(org) if r["id"] == region.id)


@router.delete("/{org_id}/regions/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_region(org_id: uuid.UUID, region_id: int, user: CurrentUser, orgs: Orgs) -> None:
    org = _call(orgs.get_for, org_id, user, admin=True)
    _call(orgs.remove_region, org, region_id)


# --- Watchlist -------------------------------------------------------------------------


@router.get("/{org_id}/watchlist")
def watchlist(org_id: uuid.UUID, user: CurrentUser, orgs: Orgs) -> list[WatchOut]:
    org = _call(orgs.get_for, org_id, user)
    return [
        WatchOut(
            taxon=TaxonOut.build(e.taxon, user.locale),
            category=WatchCategory(e.category),
            note=e.note,
        )
        for e in orgs.watchlist(org)
    ]


@router.put("/{org_id}/watchlist/{taxon_id}")
def watch(
    org_id: uuid.UUID, taxon_id: int, body: WatchIn, user: CurrentUser, orgs: Orgs
) -> WatchOut:
    org = _call(orgs.get_for, org_id, user)
    entry = _call(orgs.set_watch, org, taxon_id, body.category, body.note)
    return WatchOut(
        taxon=TaxonOut.build(entry.taxon, user.locale),
        category=WatchCategory(entry.category),
        note=entry.note,
    )


@router.delete("/{org_id}/watchlist/{taxon_id}", status_code=status.HTTP_204_NO_CONTENT)
def unwatch(org_id: uuid.UUID, taxon_id: int, user: CurrentUser, orgs: Orgs) -> None:
    orgs.remove_watch(_call(orgs.get_for, org_id, user), taxon_id)


# --- Dashboard -------------------------------------------------------------------------


def dashboard_scope(
    org_id: uuid.UUID,
    user: CurrentUser,
    orgs: Orgs,
    region_id: int | None = None,
    start: date | None = None,
    end: Annotated[date | None, Query(description="Inclusive")] = None,
    group: TaxonGroup | None = None,
    taxon_id: int | None = None,
) -> Scope:
    org = _call(orgs.get_for, org_id, user)
    if region_id is not None and region_id not in {r.id for r in org.regions}:
        raise HTTPException(404, detail="Region not found")
    return Scope(
        org_id=org.id,
        region_id=region_id,
        start=datetime.combine(start, time.min, UTC) if start else None,
        end=datetime.combine(end + timedelta(days=1), time.min, UTC) if end else None,
        group=group.value if group else None,
        taxon_id=taxon_id,
    )


ScopeDep = Annotated[Scope, Depends(dashboard_scope)]


def dashboard(db: DbSession, storage: Storage, user: CurrentUser) -> DashboardService:
    return DashboardService(db, storage, user.locale)


Dashboard = Annotated[DashboardService, Depends(dashboard)]


@router.get("/{org_id}/dashboard/summary")
def summary(scope: ScopeDep, board: Dashboard) -> dict[str, Any]:
    return board.summary(scope)


@router.get("/{org_id}/dashboard/observations")
def observations(
    scope: ScopeDep, board: Dashboard, limit: Annotated[int, Query(ge=1, le=5000)] = 2000
) -> dict[str, Any]:
    return board.points(scope, limit)


@router.get("/{org_id}/dashboard/grid")
def grid(scope: ScopeDep, board: Dashboard, step: float = 0.5) -> dict[str, Any]:
    if step not in GRID_STEPS:
        raise HTTPException(422, detail=f"step must be one of {GRID_STEPS}")
    return board.grid(scope, step)


@router.get("/{org_id}/dashboard/trends")
def trends(scope: ScopeDep, board: Dashboard) -> list[dict[str, Any]]:
    return board.trends(scope)


@router.get("/{org_id}/dashboard/changes")
def changes(scope: ScopeDep, board: Dashboard) -> dict[str, Any]:
    return board.changes(scope)


@router.get("/{org_id}/dashboard/rare")
def rare(scope: ScopeDep, board: Dashboard) -> list[dict[str, Any]]:
    return board.rare(scope)


@router.get("/{org_id}/dashboard/invasive")
def invasive(scope: ScopeDep, board: Dashboard) -> list[dict[str, Any]]:
    return board.invasive(scope)


@router.get("/{org_id}/dashboard/new-records")
def new_records(scope: ScopeDep, board: Dashboard) -> list[dict[str, Any]]:
    return board.new_records(scope)
