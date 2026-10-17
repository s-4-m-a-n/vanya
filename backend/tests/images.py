"""Synthetic test photos with optional EXIF."""

import io

from PIL import ExifTags, Image


def make_jpeg(
    size: tuple[int, int] = (800, 600),
    color: tuple[int, int, int] = (40, 120, 60),
    gps: tuple[float, float] | None = None,
    taken_at: str | None = None,
    fmt: str = "JPEG",
) -> bytes:
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    if taken_at:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken_at
    if gps:
        lat, lon = gps
        info = exif.get_ifd(ExifTags.IFD.GPSInfo)
        info[ExifTags.GPS.GPSLatitudeRef] = "N" if lat >= 0 else "S"
        info[ExifTags.GPS.GPSLatitude] = _dms(abs(lat))
        info[ExifTags.GPS.GPSLongitudeRef] = "E" if lon >= 0 else "W"
        info[ExifTags.GPS.GPSLongitude] = _dms(abs(lon))
    buffer = io.BytesIO()
    image.save(buffer, fmt, exif=exif)
    return buffer.getvalue()


def _dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = round(((value - degrees) * 60 - minutes) * 60, 4)
    return (float(degrees), float(minutes), seconds)
