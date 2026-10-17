"""GeoJSON polygons for regions of interest and restricted areas."""

import json
from typing import Any

from sqlalchemy import func

MAX_VERTICES = 20_000


class InvalidGeometry(ValueError):
    pass


def to_multipolygon(data: dict[str, Any]) -> dict[str, Any]:
    """Merge a Feature, FeatureCollection or bare (Multi)Polygon into one MultiPolygon."""
    if not isinstance(data, dict):
        raise InvalidGeometry("GeoJSON must be an object")
    if data.get("type") == "FeatureCollection":
        geometries = [f.get("geometry") for f in data.get("features") or []]
    elif data.get("type") == "Feature":
        geometries = [data.get("geometry")]
    else:
        geometries = [data]
    polygons: list = []
    for geometry in geometries:
        kind = (geometry or {}).get("type")
        if kind == "Polygon":
            polygons.append(geometry["coordinates"])
        elif kind == "MultiPolygon":
            polygons.extend(geometry["coordinates"])
        else:
            raise InvalidGeometry(f"Only Polygon and MultiPolygon are supported, not {kind}")
    if not polygons:
        raise InvalidGeometry("No polygons found")
    vertices = 0
    for polygon in polygons:
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise InvalidGeometry("Each ring needs at least 4 points and must be closed")
            for lon, lat, *_ in ring:
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise InvalidGeometry("Coordinates must be [longitude, latitude] in degrees")
            vertices += len(ring)
    if vertices > MAX_VERTICES:
        raise InvalidGeometry(f"Too many vertices (max {MAX_VERTICES}); simplify the shape")
    return {"type": "MultiPolygon", "coordinates": polygons}


def multipolygon_sql(geometry: dict[str, Any]):  # noqa: ANN201
    """SQL expression for a valid SRID-4326 MultiPolygon (self-intersections repaired)."""
    shape = func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(geometry)), 4326)
    return func.ST_Multi(func.ST_CollectionExtract(func.ST_MakeValid(shape), 3))
