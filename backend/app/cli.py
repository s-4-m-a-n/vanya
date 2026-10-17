"""Admin commands: `uv run python -m app.cli --help`."""

import argparse
import getpass
import json
import secrets
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

import app.handlers  # noqa: F401  (registers event handlers)
from app.adapters.analytics import get_analytics
from app.db import get_sessionmaker
from app.models import (
    MemberRole,
    Observation,
    ObservationStatus,
    Organisation,
    OrganisationKind,
    RejectReason,
    RestrictedArea,
    ReviewDecision,
    Role,
    Taxon,
    User,
)
from app.security import hash_password
from app.services.commonness import LOCAL_STEP_DEG
from app.services.geometry import InvalidGeometry, multipolygon_sql, to_multipolygon
from app.services.geoprivacy import apply_geoprivacy
from app.services.organisations import OrganisationService
from app.services.taxon_status import refresh_taxon_observations
from app.services.verification import (
    build_rarity_service,
    build_status_service,
    build_verification_service,
)

# Simulated experts for demos (PRD §12: the expert queue may be simulated; label it).
SIMULATED_EXPERTS = [
    ("expert1@example.org", "Expert 1 (demo)"),
    ("expert2@example.org", "Expert 2 (demo)"),
]
# Integrity flags a simulated expert treats as grounds for rejection.
SIM_REJECT = {
    "duplicate_other": RejectReason.DUPLICATE_OR_STOCK,
    "near_duplicate_other": RejectReason.DUPLICATE_OR_STOCK,
    "screenshot": RejectReason.DUPLICATE_OR_STOCK,
    "watermark": RejectReason.DUPLICATE_OR_STOCK,
    "illustration": RejectReason.NOT_AN_ORGANISM,
    "captive_or_cultivated": RejectReason.NOT_WILD,
    "location_mismatch": RejectReason.WRONG_LOCATION_OR_TIME,
    "time_mismatch": RejectReason.WRONG_LOCATION_OR_TIME,
}

DEFAULT_TAXA_FILE = Path(__file__).parent / "seed_data" / "taxa.json"


def seed_taxa(path: Path) -> int:
    # Curated statuses: never overwritten by automatic IUCN lookups.
    rows = [
        {"sensitivity": None, **row, "status_source": "seed"}
        for row in json.loads(path.read_text())["taxa"]
    ]
    stmt = insert(Taxon).values(rows)
    keep_admin_status = Taxon.status_source == "manual"
    stmt = stmt.on_conflict_do_update(
        index_elements=[Taxon.scientific_name],
        set_={
            **{
                col: stmt.excluded[col]
                for col in rows[0]
                if col not in ("scientific_name", "iucn_status", "status_source", "sensitivity")
            },
            # Re-seeding must never undo an admin's decisions.
            "iucn_status": case(
                (keep_admin_status, Taxon.iucn_status), else_=stmt.excluded.iucn_status
            ),
            "status_source": case(
                (keep_admin_status, Taxon.status_source), else_=stmt.excluded.status_source
            ),
            "sensitivity": func.coalesce(Taxon.sensitivity, stmt.excluded.sensitivity),
        },
    )
    with get_sessionmaker()() as db:
        db.execute(stmt)
        db.commit()
    return len(rows)


def create_user(email: str, password: str, display_name: str, role: Role) -> User:
    with get_sessionmaker()() as db:
        if db.scalar(select(User.id).where(User.email == email.lower())):
            raise SystemExit(f"User {email} already exists")
        user = User(
            email=email.lower(),
            display_name=display_name,
            password_hash=hash_password(password),
            role=role.value,
        )
        db.add(user)
        db.commit()
        return user


def set_role(email: str, role: Role) -> User:
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            raise SystemExit(f"No user {email}")
        user.role = role.value
        db.commit()
        return user


def set_password(email: str, password: str) -> User:
    """Reset a forgotten password (there is no email-based reset yet)."""
    if not 8 <= len(password) <= 128:
        raise SystemExit("Password must be 8-128 characters")
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.email == email.lower(), User.deleted_at.is_(None)))
        if user is None:
            raise SystemExit(f"No user {email}")
        user.password_hash = hash_password(password)
        db.commit()
        return user


def route_pending() -> int:
    """Re-run routing for records stuck in `submitted` (e.g. a handler failed)."""
    with get_sessionmaker()() as db:
        ids = db.scalars(
            select(Observation.id).where(Observation.status == ObservationStatus.SUBMITTED.value)
        ).all()
    for observation_id in ids:
        with get_sessionmaker()() as db:
            build_verification_service(db).route(observation_id)
    return len(ids)


def _simulated_experts(db) -> list[User]:  # noqa: ANN001
    experts = []
    for email, name in SIMULATED_EXPERTS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                display_name=name,
                # Random and discarded: these accounts are never signed into.
                password_hash=hash_password(secrets.token_urlsafe(32)),
                role=Role.VERIFIER.value,
            )
            db.add(user)
        experts.append(user)
    db.commit()
    return experts


def simulate_reviews(limit: int) -> list[tuple[str, str]]:
    """Clearly labelled demo experts work through the queue, oldest first.

    They reject when a hard integrity flag gives a reason; otherwise they agree with the
    species a real expert already confirmed, or else the observer's pick.
    Returns (observation id, final status) pairs.
    """
    results = []
    with get_sessionmaker()() as db:
        experts = _simulated_experts(db)
        ids = db.scalars(
            select(Observation.id)
            .where(Observation.status == ObservationStatus.IN_REVIEW.value)
            .order_by(Observation.created_at)
            .limit(limit)
        ).all()
    for observation_id in ids:
        for expert in experts:
            with get_sessionmaker()() as db:
                service = build_verification_service(db)
                obs = db.get(Observation, observation_id)
                reviewer = db.get(User, expert.id)
                if (
                    obs is None
                    or obs.status != ObservationStatus.IN_REVIEW.value
                    or obs.user_id == reviewer.id
                    or any(r.reviewer_id == reviewer.id for r in obs.reviews)
                ):
                    continue
                hard = [f["code"] for f in obs.integrity_flags if f["severity"] == "hard"]
                reason = next((SIM_REJECT[c] for c in hard if c in SIM_REJECT), None)
                confirmed = Counter(r.taxon_id for r in obs.reviews if r.decision == "confirm")
                service.review(
                    observation_id,
                    reviewer,
                    ReviewDecision.REJECT if reason else ReviewDecision.CONFIRM,
                    taxon_id=confirmed.most_common(1)[0][0] if confirmed else None,
                    reason=reason,
                    comment="Simulated review (demo data).",
                )
        with get_sessionmaker()() as db:
            results.append((str(observation_id), db.get(Observation, observation_id).status))
    return results


def recompute_rarity() -> int:
    """Refresh rarity for every (species, cell) with verified records.

    Run on a schedule: Cloud Scheduler -> Cloud Run Job on GCP, cron locally.
    """
    cells = get_analytics().species_cells(LOCAL_STEP_DEG)
    for cell in cells:
        with get_sessionmaker()() as db:
            taxon = db.get(Taxon, cell.taxon_id)
            if taxon is not None:
                build_rarity_service(db).compute(taxon, cell.cell_lat, cell.cell_lon)
                db.commit()
    return len(cells)


def add_restricted_area(name: str, geojson: Path, reason: str | None) -> RestrictedArea:
    try:
        geometry = to_multipolygon(json.loads(geojson.read_text()))
    except InvalidGeometry as exc:
        raise SystemExit(str(exc)) from exc
    with get_sessionmaker()() as db:
        area = RestrictedArea(name=name, reason=reason, area=multipolygon_sql(geometry))
        db.add(area)
        db.commit()
        return area


def list_restricted_areas() -> list[tuple[int, str, bool, float]]:
    with get_sessionmaker()() as db:
        rows = db.execute(
            select(
                RestrictedArea.id,
                RestrictedArea.name,
                RestrictedArea.active,
                func.ST_Area(func.Geography(RestrictedArea.area)) / 1e6,
            ).order_by(RestrictedArea.id)
        ).all()
    return [tuple(r) for r in rows]


def _taxon_by_name(db, name: str) -> Taxon:  # noqa: ANN001
    taxon = db.scalar(select(Taxon).where(func.lower(Taxon.scientific_name) == name.lower()))
    if taxon is None:
        raise SystemExit(f"Unknown species: {name}")
    return taxon


def set_sensitivity(scientific_name: str, level: str | None) -> int:
    """Override how a species' locations are shown ("hidden" for poaching targets)."""
    with get_sessionmaker()() as db:
        taxon = _taxon_by_name(db, scientific_name)
        taxon.sensitivity = level
        updated = refresh_taxon_observations(db, taxon)
        db.commit()
        return updated


def refresh_statuses() -> tuple[int, int]:
    """Look up the IUCN category of every species whose status was never checked."""
    with get_sessionmaker()() as db:
        ids = db.scalars(select(Taxon.id).where(Taxon.status_source.is_(None))).all()
    checked = 0
    for taxon_id in ids:
        with get_sessionmaker()() as db:
            taxon = db.get(Taxon, taxon_id)
            if build_status_service(db).refresh(taxon):
                refresh_taxon_observations(db, taxon)
                checked += 1
            db.commit()
    return checked, len(ids)


def check_seed_statuses() -> list[tuple[str, str, str | None]]:
    """Compare curated seed statuses with IUCN via GBIF (PRD §13 Q2). Changes nothing."""
    mismatches = []
    with get_sessionmaker()() as db:
        service = build_status_service(db)
        for taxon in db.scalars(select(Taxon).where(Taxon.status_source == "seed")):
            current = service.lookup(taxon)
            if current != taxon.iucn_status:
                mismatches.append((taxon.scientific_name, taxon.iucn_status, current))
        db.commit()  # keeps any GBIF keys looked up along the way
    return mismatches


def refresh_geoprivacy() -> int:
    """Recompute generalised locations for every observation (after rule changes)."""
    with get_sessionmaker()() as db:
        observations = db.scalars(select(Observation)).all()
        for obs in observations:
            apply_geoprivacy(obs, obs.taxon)
        db.commit()
        return len(observations)


def create_org(name: str, kind: str) -> str:
    with get_sessionmaker()() as db:
        return str(OrganisationService(db).create(name, kind).id)


def _org_by_name(db, name: str) -> Organisation:  # noqa: ANN001
    org = db.scalar(select(Organisation).where(Organisation.name == name))
    if org is None:
        raise SystemExit(f"Unknown organisation: {name}")
    return org


def add_org_member(org_name: str, email: str, role: str) -> None:
    with get_sessionmaker()() as db:
        OrganisationService(db).add_member(_org_by_name(db, org_name), email, MemberRole(role))


def add_region(org_name: str, name: str, geojson: Path) -> int:
    with get_sessionmaker()() as db:
        try:
            region = OrganisationService(db).add_region(
                _org_by_name(db, org_name), name, json.loads(geojson.read_text())
            )
        except InvalidGeometry as exc:
            raise SystemExit(str(exc)) from exc
        return region.id


def list_orgs() -> list[tuple[str, str, int, int]]:
    with get_sessionmaker()() as db:
        return [
            (o.name, o.kind, len(o.members), len(o.regions))
            for o in db.scalars(select(Organisation).order_by(Organisation.name))
        ]


def generate_reports(days: int, locale: str) -> list[tuple[str, str]]:
    """Write a report for every organisation (schedule it: Cloud Scheduler on GCP)."""
    from datetime import date, timedelta

    from app.adapters.llm import get_llm
    from app.adapters.storage import get_storage
    from app.services.analyst import AnalystService

    end = date.today()
    start = end - timedelta(days=days - 1)
    done = []
    with get_sessionmaker()() as db:
        for org in db.scalars(select(Organisation).order_by(Organisation.name)).all():
            report = AnalystService(db, get_llm, get_storage()).report(
                org, start, end, locale, None
            )
            done.append((org.name, report.content.get("title", "")))
    return done


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-taxa", help="Upsert the taxonomy reference table")
    seed.add_argument("--file", type=Path, default=DEFAULT_TAXA_FILE)

    user = sub.add_parser("create-user", help="Create a user with any role")
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", type=Role, choices=list(Role), default=Role.OBSERVER)
    user.add_argument("--password", help="Prompted for if omitted")

    role = sub.add_parser("set-role", help="Change a user's role (e.g. make yourself a verifier)")
    role.add_argument("--email", required=True)
    role.add_argument("--role", type=Role, choices=list(Role), required=True)

    reset = sub.add_parser("set-password", help="Reset a user's password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", help="Prompted for if omitted")

    sub.add_parser("route-pending", help="Re-run verification routing for stuck submissions")

    sim = sub.add_parser("simulate-reviews", help="Demo only: simulated experts review the queue")
    sim.add_argument("--limit", type=int, default=20)

    sub.add_parser("recompute-rarity", help="Refresh rarity scores (run on a schedule)")

    area = sub.add_parser(
        "add-restricted-area", help="Add a no-rewards zone from a GeoJSON polygon file"
    )
    area.add_argument("--name", required=True)
    area.add_argument("--geojson", type=Path, required=True)
    area.add_argument("--reason")

    sub.add_parser("list-restricted-areas", help="Show restricted areas")

    sens = sub.add_parser(
        "set-sensitivity", help="Override how a species' locations are shown publicly"
    )
    sens.add_argument("--species", required=True, help="Scientific name")
    sens.add_argument(
        "--level",
        choices=["open", "obscured", "hidden", "default"],
        required=True,
        help="'default' follows the conservation status again",
    )

    sub.add_parser("refresh-statuses", help="Look up IUCN categories for unchecked species")
    sub.add_parser("check-seed-statuses", help="Compare seed statuses with IUCN (read-only)")
    sub.add_parser("refresh-geoprivacy", help="Recompute generalised locations")

    org = sub.add_parser("create-org", help="Create an organisation workspace")
    org.add_argument("--name", required=True)
    org.add_argument("--kind", choices=[k.value for k in OrganisationKind], default="other")

    member = sub.add_parser("add-org-member", help="Add (or re-role) an organisation member")
    member.add_argument("--org", required=True, help="Organisation name")
    member.add_argument("--email", required=True)
    member.add_argument("--role", choices=[r.value for r in MemberRole], default="analyst")

    region = sub.add_parser("add-region", help="Add a region of interest from GeoJSON")
    region.add_argument("--org", required=True, help="Organisation name")
    region.add_argument("--name", required=True)
    region.add_argument("--geojson", type=Path, required=True)

    sub.add_parser("list-orgs", help="Show organisations")

    reports = sub.add_parser("generate-reports", help="Write a report for every organisation")
    reports.add_argument("--days", type=int, default=30)
    reports.add_argument("--locale", choices=["en", "hi", "es"], default="en")

    args = parser.parse_args(argv)
    if args.command == "seed-taxa":
        print(f"Upserted {seed_taxa(args.file)} taxa")
    elif args.command == "create-user":
        password = args.password or getpass.getpass("Password: ")
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters")
        created = create_user(args.email, password, args.name, args.role)
        print(f"Created {created.role} {created.email} ({created.id})")
    elif args.command == "set-role":
        updated = set_role(args.email, args.role)
        print(f"{updated.email} is now {updated.role}")
    elif args.command == "set-password":
        password = args.password or getpass.getpass("New password: ")
        print(f"Password updated for {set_password(args.email, password).email}")
    elif args.command == "route-pending":
        print(f"Routed {route_pending()} submission(s)")
    elif args.command == "simulate-reviews":
        results = simulate_reviews(args.limit)
        print(f"SIMULATED expert reviews on {len(results)} observation(s):")
        for observation_id, status in results:
            print(f"  {observation_id}: {status}")
    elif args.command == "recompute-rarity":
        print(f"Recomputed rarity for {recompute_rarity()} species/cell pair(s)")
    elif args.command == "add-restricted-area":
        created = add_restricted_area(args.name, args.geojson, args.reason)
        print(f"Added restricted area #{created.id}: {created.name}")
    elif args.command == "set-sensitivity":
        level = None if args.level == "default" else args.level
        print(
            f"{args.species}: {args.level} ({set_sensitivity(args.species, level)} records updated)"
        )
    elif args.command == "refresh-statuses":
        checked, total = refresh_statuses()
        print(f"Checked {checked} of {total} unchecked species")
    elif args.command == "check-seed-statuses":
        mismatches = check_seed_statuses()
        print(f"{len(mismatches)} seed status(es) differ from IUCN via GBIF:")
        for name, seed, iucn in mismatches:
            print(f"  {name}: seed {seed} vs IUCN {iucn or 'unavailable'}")
    elif args.command == "refresh-geoprivacy":
        print(f"Updated {refresh_geoprivacy()} observation(s)")
    elif args.command == "create-org":
        print(f"Created organisation {args.name} ({create_org(args.name, args.kind)})")
    elif args.command == "add-org-member":
        add_org_member(args.org, args.email, args.role)
        print(f"{args.email} is now {args.role} of {args.org}")
    elif args.command == "add-region":
        print(f"Added region #{add_region(args.org, args.name, args.geojson)} to {args.org}")
    elif args.command == "list-orgs":
        for name, kind, members, regions in list_orgs():
            print(f"  {name} ({kind}): {members} member(s), {regions} region(s)")
    elif args.command == "generate-reports":
        for name, title in generate_reports(args.days, args.locale):
            print(f"  {name}: {title}")
    elif args.command == "list-restricted-areas":
        for area_id, name, active, km2 in list_restricted_areas():
            print(f"  #{area_id} {name} ({km2:,.1f} km²){'' if active else ' [inactive]'}")


if __name__ == "__main__":
    main()
