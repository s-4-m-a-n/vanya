from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select

from app.api.deps import DbSession
from app.models import Taxon, TaxonGroup
from app.schemas.taxa import TaxonOut, TaxonPage

router = APIRouter(prefix="/taxa", tags=["taxa"])


@router.get("")
def list_taxa(
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    group: TaxonGroup | None = None,
    locale: str = "en",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaxonPage:
    stmt = select(Taxon)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Taxon.scientific_name.ilike(pattern),
                cast(Taxon.common_names, String).ilike(pattern),
            )
        )
    if group:
        stmt = stmt.where(Taxon.taxon_group == group.value)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Taxon.scientific_name).limit(limit).offset(offset)).all()
    return TaxonPage(items=[TaxonOut.build(t, locale) for t in rows], total=total)


@router.get("/{taxon_id}")
def get_taxon(taxon_id: int, db: DbSession, locale: str = "en") -> TaxonOut:
    taxon = db.get(Taxon, taxon_id)
    if taxon is None:
        raise HTTPException(404, detail="Taxon not found")
    return TaxonOut.build(taxon, locale)
