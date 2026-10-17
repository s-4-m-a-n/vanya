"""Validate uploads, read EXIF, and produce stripped JPEG derivatives."""

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()  # iPhone photos (HEIC)

# Guard against decompression bombs well before Pillow's own limit.
Image.MAX_IMAGE_PIXELS = 60_000_000

ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}
STORED_MAX_PX = 2048
ID_MAX_PX = 1024


class InvalidPhoto(ValueError):
    pass


@dataclass(frozen=True)
class PhotoExif:
    latitude: float | None = None
    longitude: float | None = None
    taken_at: datetime | None = None  # timezone-aware only when the camera wrote an offset
    make: str | None = None
    model: str | None = None
    software: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "taken_at": self.taken_at.isoformat() if self.taken_at else None,
            "make": self.make,
            "model": self.model,
            "software": self.software,
        }


@dataclass(frozen=True)
class ProcessedPhoto:
    jpeg: bytes  # stored copy: EXIF stripped, longest side <= STORED_MAX_PX
    id_jpeg: bytes  # smaller copy sent to the identifier
    width: int
    height: int
    sha256: str  # of the original upload
    exif: PhotoExif
    dhash: int  # perceptual hash of the upright image, for near-duplicate detection


def process_photo(data: bytes) -> ProcessedPhoto:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise InvalidPhoto("Unsupported or corrupt image") from exc
    if image.format not in ALLOWED_FORMATS:
        raise InvalidPhoto(f"Unsupported image format: {image.format}")

    exif = read_exif(image)
    upright = ImageOps.exif_transpose(image).convert("RGB")
    stored, width, height = _to_jpeg(upright, STORED_MAX_PX, quality=85)
    id_copy, _, _ = _to_jpeg(upright, ID_MAX_PX, quality=80)
    return ProcessedPhoto(
        jpeg=stored,
        id_jpeg=id_copy,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        exif=exif,
        dhash=dhash(upright),
    )


def dhash(image: Image.Image, size: int = 8) -> int:
    """64-bit difference hash, returned signed so it fits a Postgres BIGINT.

    Survives resizing and re-encoding, so re-uploads of the same picture still match.
    """
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = gray.tobytes()
    bits = 0
    for row in range(size):
        start = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (pixels[start + col] > pixels[start + col + 1])
    return bits - (1 << 64) if bits >= 1 << 63 else bits


def _to_jpeg(image: Image.Image, max_px: int, quality: int) -> tuple[bytes, int, int]:
    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    # No exif= argument, so GPS and device metadata are not written to the copy.
    copy.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), copy.width, copy.height


def read_exif(image: Image.Image) -> PhotoExif:
    try:
        exif = image.getexif()
    except Exception:
        return PhotoExif()
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    details = exif.get_ifd(ExifTags.IFD.Exif)
    latitude = _dms(gps.get(ExifTags.GPS.GPSLatitude), gps.get(ExifTags.GPS.GPSLatitudeRef))
    longitude = _dms(gps.get(ExifTags.GPS.GPSLongitude), gps.get(ExifTags.GPS.GPSLongitudeRef))
    # Some cameras write 0,0 when they have no fix.
    if (
        latitude is None
        or longitude is None
        or (latitude == 0 and longitude == 0)
        or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
    ):
        latitude = longitude = None
    return PhotoExif(
        latitude=latitude,
        longitude=longitude,
        taken_at=_exif_datetime(
            details.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime),
            details.get(ExifTags.Base.OffsetTimeOriginal),
        ),
        make=_text(exif.get(ExifTags.Base.Make)),
        model=_text(exif.get(ExifTags.Base.Model)),
        software=_text(exif.get(ExifTags.Base.Software)),
    )


def _dms(value: Any, ref: Any) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    decimal = degrees + minutes / 60 + seconds / 3600
    return round(-decimal if str(ref).upper() in ("S", "W") else decimal, 6)


def _exif_datetime(value: Any, offset: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset_text = _text(offset)
    if offset_text:
        try:
            return datetime.strptime(f"{text} {offset_text}", "%Y:%m:%d %H:%M:%S %z")
        except ValueError:
            pass
    return parsed


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    cleaned = str(value).strip("\x00 ").strip()
    return cleaned[:100] or None
