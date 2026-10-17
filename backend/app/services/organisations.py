"""Organisation workspaces (PRD §9: multi-tenant), regions of interest and watchlists."""

import json
import uuid
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    MemberRole,
    Observation,
    Organisation,
    OrganisationMember,
    RegionOfInterest,
    Role,
    Taxon,
    User,
    WatchCategory,
    WatchlistEntry,
)
from app.services.geometry import multipolygon_sql, to_multipolygon
from app.services.observations import ObservationError


def not_found() -> ObservationError:
    return ObservationError("not_found", "Organisation not found", status_code=404)


class OrganisationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Access --------------------------------------------------------------------------

    def mine(self, user: User) -> list[tuple[Organisation, str]]:
        if user.role == Role.ADMIN.value:
            orgs = self.db.scalars(select(Organisation).order_by(Organisation.name)).all()
            roles = {
                m.organisation_id: m.role
                for m in self.db.scalars(
                    select(OrganisationMember).where(OrganisationMember.user_id == user.id)
                )
            }
            return [(o, roles.get(o.id, MemberRole.ADMIN.value)) for o in orgs]
        rows = self.db.execute(
            select(Organisation, OrganisationMember.role)
            .join(OrganisationMember, OrganisationMember.organisation_id == Organisation.id)
            .where(OrganisationMember.user_id == user.id)
            .order_by(Organisation.name)
        ).all()
        return [(org, role) for org, role in rows]

    def get_for(self, org_id: uuid.UUID, user: User, admin: bool = False) -> Organisation:
        """The organisation if the user may see it (or manage it, when admin=True).

        Non-members get 404 rather than 403 so organisation ids can't be probed.
        """
        org = self.db.get(Organisation, org_id)
        if org is None:
            raise not_found()
        if user.role == Role.ADMIN.value:
            return org
        member = self.db.get(OrganisationMember, (org_id, user.id))
        if member is None:
            raise not_found()
        if admin and member.role != MemberRole.ADMIN.value:
            raise ObservationError("org_admin_required", "Organisation admins only", 403)
        return org

    def covers(self, user: User, obs: Observation) -> bool:
        """Is the record inside a region of one of the user's organisations?

        Uses the generalised point, so region shape can't be used to locate a record
        more precisely than its protection level allows.
        """
        return bool(
            self.db.scalar(
                select(
                    exists().where(
                        OrganisationMember.user_id == user.id,
                        RegionOfInterest.organisation_id == OrganisationMember.organisation_id,
                        func.ST_Intersects(RegionOfInterest.area, obs.generalised_location),
                    )
                )
            )
        )

    # --- Management ----------------------------------------------------------------------

    def create(self, name: str, kind: str, creator: User | None = None) -> Organisation:
        if self.db.scalar(select(Organisation.id).where(Organisation.name == name)):
            raise ObservationError("org_exists", "An organisation with that name exists", 409)
        org = Organisation(name=name, kind=kind)
        self.db.add(org)
        self.db.flush()
        if creator is not None:
            self.db.add(
                OrganisationMember(
                    organisation_id=org.id, user_id=creator.id, role=MemberRole.ADMIN.value
                )
            )
        self.db.commit()
        return org

    def add_member(self, org: Organisation, email: str, role: MemberRole) -> OrganisationMember:
        user = self.db.scalar(
            select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
        )
        if user is None:
            raise ObservationError("user_not_found", "No user with that email", 404)
        member = self.db.get(OrganisationMember, (org.id, user.id))
        if member is None:
            member = OrganisationMember(organisation_id=org.id, user_id=user.id, role=role.value)
            self.db.add(member)
        else:
            member.role = role.value
        # Members need the analyst role for audited exact locations in their regions.
        if user.role == Role.OBSERVER.value:
            user.role = Role.ANALYST.value
        self.db.commit()
        self.db.refresh(member)
        return member

    def remove_member(self, org: Organisation, user_id: uuid.UUID) -> None:
        member = self.db.get(OrganisationMember, (org.id, user_id))
        if member is None:
            raise ObservationError("not_found", "Not a member", 404)
        admins = [m for m in org.members if m.role == MemberRole.ADMIN.value]
        if member.role == MemberRole.ADMIN.value and len(admins) == 1:
            raise ObservationError("last_admin", "An organisation needs at least one admin", 409)
        self.db.delete(member)
        self.db.commit()

    def add_region(self, org: Organisation, name: str, geojson: dict[str, Any]) -> RegionOfInterest:
        region = RegionOfInterest(
            organisation_id=org.id, name=name, area=multipolygon_sql(to_multipolygon(geojson))
        )
        self.db.add(region)
        self.db.commit()
        self.db.refresh(region)
        return region

    def remove_region(self, org: Organisation, region_id: int) -> None:
        region = self.db.get(RegionOfInterest, region_id)
        if region is None or region.organisation_id != org.id:
            raise ObservationError("not_found", "Region not found", 404)
        self.db.delete(region)
        self.db.commit()

    def region_geojson(self, org: Organisation) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(
                RegionOfInterest.id,
                RegionOfInterest.name,
                func.ST_AsGeoJSON(RegionOfInterest.area, 6),
                func.ST_Area(func.Geography(RegionOfInterest.area)) / 1e6,
            )
            .where(RegionOfInterest.organisation_id == org.id)
            .order_by(RegionOfInterest.name)
        ).all()
        return [
            {"id": rid, "name": name, "geometry": json.loads(geo), "area_km2": round(km2, 1)}
            for rid, name, geo, km2 in rows
        ]

    def set_watch(
        self, org: Organisation, taxon_id: int, category: WatchCategory, note: str | None
    ) -> WatchlistEntry:
        if self.db.get(Taxon, taxon_id) is None:
            raise ObservationError("unknown_taxon", f"Unknown taxon {taxon_id}")
        entry = self.db.get(WatchlistEntry, (org.id, taxon_id))
        if entry is None:
            entry = WatchlistEntry(organisation_id=org.id, taxon_id=taxon_id)
            self.db.add(entry)
        entry.category, entry.note = category.value, note
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def remove_watch(self, org: Organisation, taxon_id: int) -> None:
        entry = self.db.get(WatchlistEntry, (org.id, taxon_id))
        if entry is not None:
            self.db.delete(entry)
            self.db.commit()

    def watchlist(self, org: Organisation) -> list[WatchlistEntry]:
        return list(
            self.db.scalars(
                select(WatchlistEntry)
                .where(WatchlistEntry.organisation_id == org.id)
                .order_by(WatchlistEntry.category, WatchlistEntry.created_at)
            )
        )
