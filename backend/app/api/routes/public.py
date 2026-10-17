"""Public, unauthenticated views (PRD F5: generalised locations only).

Verified records only; no observer identities, no photos, and protected species at
grid-cell precision. Hidden records are left out entirely.
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.models import Geoprivacy, Observation, ObservationStatus, Taxon, TaxonGroup

router = APIRouter(prefix="/public", tags=["public"])

MAX_BBOX_DEGREES = 20  # keeps a single request from dumping the whole dataset


@router.get("/observations")
def public_observations(
    response: Response,
    db: DbSession,
    bbox: Annotated[str, Query(description="minLon,minLat,maxLon,maxLat")],
    taxon_id: int | None = None,
    group: TaxonGroup | None = None,
    locale: str = "en",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(422, detail="bbox must be minLon,minLat,maxLon,maxLat") from exc
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise HTTPException(422, detail="Invalid bbox")
    if max_lon - min_lon > MAX_BBOX_DEGREES or max_lat - min_lat > MAX_BBOX_DEGREES:
        raise HTTPException(422, detail=f"bbox may span at most {MAX_BBOX_DEGREES}°")

    stmt = (
        select(Observation, Taxon)
        .join(Taxon, Observation.taxon_id == Taxon.id)
        .where(
            Observation.status == ObservationStatus.VERIFIED.value,
            Observation.geoprivacy.in_([Geoprivacy.OPEN.value, Geoprivacy.OBSCURED.value]),
            func.ST_Intersects(
                Observation.generalised_location,
                func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326),
            ),
        )
        .order_by(Observation.observed_at.desc())
        .limit(limit)
    )
    if taxon_id is not None:
        stmt = stmt.where(Observation.taxon_id == taxon_id)
    if group is not None:
        stmt = stmt.where(Taxon.taxon_group == group.value)

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [obs.generalised_longitude, obs.generalised_latitude],
            },
            "properties": {
                "id": str(obs.id),
                "scientific_name": taxon.scientific_name,
                "common_name": taxon.common_name(locale),
                "taxon_id": taxon.id,
                "taxon_group": taxon.taxon_group,
                "iucn_status": taxon.iucn_status,
                # Date only: the time of day adds little and narrows down a visit.
                "observed_on": obs.observed_at.date().isoformat(),
                "precision": "generalised" if obs.geoprivacy == "obscured" else "approximate",
                "accuracy_m": obs.generalised_accuracy_m,
                "rarity_tier": obs.rarity_tier,
            },
        }
        for obs, taxon in db.execute(stmt).all()
    ]
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"type": "FeatureCollection", "features": features}
