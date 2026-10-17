"""Integrity checks (PRD F3): duplicates, EXIF/GPS/time consistency, suspicious photos.

Hard flags send a record to expert review; soft flags are shown to reviewers only.
"""

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.orm import Session

from app.models import Observation
from app.services.photos import PhotoExif

Severity = Literal["hard", "soft"]

# Identifier photo flags that mean "probably not a genuine wild sighting".
HARD_PHOTO_FLAGS = {"screenshot", "illustration", "watermark", "captive_or_cultivated"}
EDITING_SOFTWARE = ("photoshop", "lightroom", "gimp", "snapseed", "picsart", "pixelmator")
DUPLICATE_CODES = {"duplicate_own", "duplicate_other", "near_duplicate_other", "similar_to_own"}
LOW_ACCURACY_M = 1000


@dataclass(frozen=True)
class Flag:
    code: str
    severity: Severity
    detail: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Flag":
        return cls(code=data["code"], severity=data["severity"], detail=data.get("detail"))


def capture_flags(
    exif: PhotoExif,
    exif_time: datetime | None,
    device_latitude: float | None,
    device_longitude: float | None,
    accuracy_m: float | None,
    device_time: datetime | None,
    photo_flags: list[str],
    location_mismatch_km: float,
    time_mismatch_hours: float,
) -> list[Flag]:
    """Checks that need the upload itself; run once when the photo arrives.

    `exif_time` is the EXIF capture time already converted to UTC; `device_time` is the
    time the client reported, if any.
    """
    flags = [Flag(code, "hard") for code in sorted(set(photo_flags) & HARD_PHOTO_FLAGS)]

    if (
        device_latitude is not None
        and device_longitude is not None
        and exif.latitude is not None
        and exif.longitude is not None
    ):
        distance = haversine_km(device_latitude, device_longitude, exif.latitude, exif.longitude)
        # Allow for the device's own uncertainty.
        if distance > location_mismatch_km + (accuracy_m or 0) / 1000:
            flags.append(Flag("location_mismatch", "hard", f"{distance:.1f} km"))

    if device_time is not None and exif_time is not None:
        hours = abs((device_time - exif_time).total_seconds()) / 3600
        if hours > time_mismatch_hours:
            flags.append(Flag("time_mismatch", "hard", f"{hours:.0f} h"))

    if not (exif.make or exif.model or exif.taken_at):
        flags.append(Flag("no_camera_metadata", "soft"))
    if exif.software and any(app in exif.software.lower() for app in EDITING_SOFTWARE):
        flags.append(Flag("edited", "soft", exif.software))
    if accuracy_m is not None and accuracy_m > LOW_ACCURACY_M:
        flags.append(Flag("low_accuracy", "soft", f"±{accuracy_m:.0f} m"))
    return flags


def duplicate_flags(db: Session, observation: Observation, max_bits: int) -> list[Flag]:
    """Same photo (exact bytes) or a near-identical one (perceptual hash) elsewhere."""
    exact = db.scalars(
        select(Observation.user_id)
        .where(
            Observation.photo_sha256 == observation.photo_sha256,
            Observation.id != observation.id,
        )
        .limit(20)
    ).all()
    if exact:
        return [_by_owner(exact, observation.user_id, "duplicate_own", "duplicate_other")]

    if observation.photo_dhash is None or not _informative(observation.photo_dhash):
        return []
    distance = func.bit_count(
        cast(Observation.photo_dhash.op("#")(observation.photo_dhash), BIT(64))
    )
    similar = db.scalars(
        select(Observation.user_id)
        .where(
            Observation.id != observation.id,
            Observation.photo_dhash.is_not(None),
            distance <= max_bits,
        )
        .limit(20)
    ).all()
    if not similar:
        return []
    flag = _by_owner(similar, observation.user_id, "similar_to_own", "near_duplicate_other")
    # Burst shots of one sighting by the same person are normal; someone else's photo isn't.
    return [Flag(flag.code, "soft" if flag.code == "similar_to_own" else "hard")]


def _by_owner(user_ids: list[uuid.UUID], owner: uuid.UUID, own: str, other: str) -> Flag:
    if any(uid != owner for uid in user_ids):
        return Flag(other, "hard")
    return Flag(own, "hard")


def _informative(dhash: int) -> bool:
    """Flat images (solid colour, overexposed) hash to nearly all-0 or all-1 bits."""
    ones = bin(dhash & (2**64 - 1)).count("1")
    return 8 <= ones <= 56


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))
