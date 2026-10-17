import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select

from app.api.deps import DbSession, require_roles
from app.models import (
    IucnStatus,
    MemberRole,
    Observation,
    Organisation,
    RestrictedArea,
    Role,
    StatusSource,
    Taxon,
    User,
)
from app.models.observation import LocationAccess as LocationAccessLog
from app.schemas.auth import RoleUpdate, UserOut
from app.schemas.taxa import TaxonOut
from app.services.geometry import InvalidGeometry, multipolygon_sql, to_multipolygon
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


# --- Admin console ----------------------------------------------------------------------


class DayCount(BaseModel):
    day: date
    submitted: int
    verified: int


class TopObserver(BaseModel):
    user_id: uuid.UUID
    display_name: str
    verified: int


class OverviewOut(BaseModel):
    users: int
    users_by_role: dict[str, int]
    new_users_7d: int
    observations_by_status: dict[str, int]
    verified_7d: int
    awaiting_review: int
    organisations: int
    restricted_areas: int
    protected_species: int
    reveals_7d: int
    daily: list[DayCount]
    top_observers: list[TopObserver]


@router.get("/overview")
def overview(db: DbSession) -> OverviewOut:
    """Platform health at a glance for the admin console."""
    now = datetime.now(UTC)
    week = now - timedelta(days=7)
    live_users = User.deleted_at.is_(None)
    by_role = dict(
        db.execute(select(User.role, func.count()).where(live_users).group_by(User.role)).all()
    )
    by_status = dict(
        db.execute(select(Observation.status, func.count()).group_by(Observation.status)).all()
    )
    start = (now - timedelta(days=29)).date()
    day = func.date(Observation.created_at)
    submitted = dict(
        db.execute(
            select(day, func.count())
            .where(func.date(Observation.created_at) >= start)
            .group_by(day)
        ).all()
    )
    decided_day = func.date(Observation.decided_at)
    verified = dict(
        db.execute(
            select(decided_day, func.count())
            .where(Observation.status == "verified", func.date(Observation.decided_at) >= start)
            .group_by(decided_day)
        ).all()
    )
    top = db.execute(
        select(User.id, User.display_name, func.count(Observation.id).label("n"))
        .join(Observation, Observation.user_id == User.id)
        .where(Observation.status == "verified", live_users)
        .group_by(User.id)
        .order_by(func.count(Observation.id).desc())
        .limit(5)
    ).all()
    return OverviewOut(
        users=sum(by_role.values()),
        users_by_role=by_role,
        new_users_7d=db.scalar(select(func.count()).where(live_users, User.created_at >= week))
        or 0,
        observations_by_status=by_status,
        verified_7d=db.scalar(
            select(func.count()).where(
                Observation.status == "verified", Observation.decided_at >= week
            )
        )
        or 0,
        awaiting_review=by_status.get("in_review", 0),
        organisations=db.scalar(select(func.count(Organisation.id))) or 0,
        restricted_areas=db.scalar(
            select(func.count(RestrictedArea.id)).where(RestrictedArea.active.is_(True))
        )
        or 0,
        protected_species=db.scalar(
            select(func.count(Taxon.id)).where(Taxon.sensitivity.in_(["obscured", "hidden"]))
        )
        or 0,
        reveals_7d=db.scalar(
            select(func.count(LocationAccessLog.id)).where(LocationAccessLog.created_at >= week)
        )
        or 0,
        daily=[
            DayCount(
                day=d,
                submitted=submitted.get(d, 0),
                verified=verified.get(d, 0),
            )
            for d in (start + timedelta(days=i) for i in range(30))
        ],
        top_observers=[TopObserver(user_id=r[0], display_name=r[1], verified=r[2]) for r in top],
    )


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    reputation: int
    created_at: datetime
    observations: int
    verified: int
    last_sighting_at: datetime | None


class UserPage(BaseModel):
    items: list[AdminUserOut]
    total: int


@router.get("/users")
def list_users(
    db: DbSession,
    q: str | None = None,
    role: Role | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    stats = (
        select(
            Observation.user_id.label("user_id"),
            func.count().label("observations"),
            func.count().filter(Observation.status == "verified").label("verified"),
            func.max(Observation.created_at).label("last_sighting_at"),
        )
        .group_by(Observation.user_id)
        .subquery()
    )
    conditions = [User.deleted_at.is_(None)]
    if q:
        pattern = f"%{q.strip().lower()}%"
        conditions.append(
            or_(func.lower(User.email).like(pattern), func.lower(User.display_name).like(pattern))
        )
    if role is not None:
        conditions.append(User.role == role.value)
    total = db.scalar(select(func.count(User.id)).where(*conditions)) or 0
    rows = db.execute(
        select(User, stats.c.observations, stats.c.verified, stats.c.last_sighting_at)
        .outerjoin(stats, stats.c.user_id == User.id)
        .where(*conditions)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return UserPage(
        items=[
            AdminUserOut(
                id=u.id,
                email=u.email,
                display_name=u.display_name,
                role=Role(u.role),
                reputation=u.reputation,
                created_at=u.created_at,
                observations=n or 0,
                verified=v or 0,
                last_sighting_at=last,
            )
            for u, n, v, last in rows
        ],
        total=total,
    )


class AdminOrgOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    members: int
    regions: int
    admins: list[str]
    created_at: datetime


@router.get("/organisations")
def list_organisations(db: DbSession) -> list[AdminOrgOut]:
    return [
        AdminOrgOut(
            id=o.id,
            name=o.name,
            kind=o.kind,
            members=len(o.members),
            regions=len(o.regions),
            admins=sorted(m.user.email for m in o.members if m.role == MemberRole.ADMIN.value),
            created_at=o.created_at,
        )
        for o in db.scalars(select(Organisation).order_by(Organisation.name))
    ]


class AdminTaxonOut(BaseModel):
    taxon: TaxonOut
    sensitivity: str | None
    status_source: str | None
    observations: int


@router.get("/taxa")
def list_taxa(
    db: DbSession,
    admin: AdminUser,
    q: str | None = None,
    protected_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AdminTaxonOut]:
    counts = (
        select(Observation.taxon_id, func.count().label("n"))
        .where(Observation.taxon_id.is_not(None))
        .group_by(Observation.taxon_id)
        .subquery()
    )
    stmt = select(Taxon, counts.c.n).outerjoin(counts, counts.c.taxon_id == Taxon.id)
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Taxon.scientific_name).like(pattern),
                func.lower(cast(Taxon.common_names, String)).like(pattern),
            )
        )
    if protected_only:
        stmt = stmt.where(
            or_(
                Taxon.sensitivity.in_(["obscured", "hidden"]),
                Taxon.iucn_status.in_(["CR", "EN", "VU"]),
            )
        )
    rows = db.execute(stmt.order_by(Taxon.scientific_name).limit(limit)).all()
    return [
        AdminTaxonOut(
            taxon=TaxonOut.build(t, admin.locale),
            sensitivity=t.sensitivity,
            status_source=t.status_source,
            observations=n or 0,
        )
        for t, n in rows
    ]


class RestrictedAreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=300)
    geojson: dict[str, Any]


class RestrictedAreaOut(BaseModel):
    id: int
    name: str
    reason: str | None
    active: bool
    area_km2: float
    created_at: datetime


class RestrictedAreaUpdate(BaseModel):
    active: bool


def _areas(db, where=None) -> list[RestrictedAreaOut]:  # noqa: ANN001
    stmt = select(RestrictedArea, func.ST_Area(func.Geography(RestrictedArea.area)) / 1e6).order_by(
        RestrictedArea.id
    )
    if where is not None:
        stmt = stmt.where(where)
    return [
        RestrictedAreaOut(
            id=a.id,
            name=a.name,
            reason=a.reason,
            active=a.active,
            area_km2=round(km2, 1),
            created_at=a.created_at,
        )
        for a, km2 in db.execute(stmt).all()
    ]


@router.get("/restricted-areas")
def restricted_areas(db: DbSession) -> list[RestrictedAreaOut]:
    """No-reward zones (e.g. sensitive breeding sites) where XP isn't awarded."""
    return _areas(db)


@router.post("/restricted-areas", status_code=201)
def add_restricted_area(body: RestrictedAreaIn, db: DbSession) -> RestrictedAreaOut:
    try:
        geometry = to_multipolygon(body.geojson)
    except InvalidGeometry as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    area = RestrictedArea(name=body.name, reason=body.reason, area=multipolygon_sql(geometry))
    db.add(area)
    db.commit()
    return _areas(db, RestrictedArea.id == area.id)[0]


@router.patch("/restricted-areas/{area_id}")
def update_restricted_area(
    area_id: int, body: RestrictedAreaUpdate, db: DbSession
) -> RestrictedAreaOut:
    area = db.get(RestrictedArea, area_id)
    if area is None:
        raise HTTPException(404, detail="Restricted area not found")
    area.active = body.active
    db.commit()
    return _areas(db, RestrictedArea.id == area_id)[0]
