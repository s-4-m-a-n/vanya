import io
import random
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from app.services.integrity import _informative, capture_flags, haversine_km
from app.services.photos import PhotoExif, dhash

T0 = datetime(2026, 10, 16, 7, 0, tzinfo=UTC)
CAMERA = PhotoExif(latitude=27.7, longitude=85.3, taken_at=T0, make="Pixel", model="8")


def flags(exif=CAMERA, exif_time=T0, lat=27.7, lon=85.3, accuracy=10.0, device_time=T0, photo=()):
    return {
        f.code: f.severity
        for f in capture_flags(
            exif, exif_time, lat, lon, accuracy, device_time, list(photo), 5.0, 24.0
        )
    }


def test_consistent_capture_has_no_flags():
    assert flags() == {}


def test_photo_taken_far_from_claimed_location_is_flagged():
    assert flags(lat=28.7, lon=77.1)["location_mismatch"] == "hard"  # Delhi vs Kathmandu


def test_location_tolerance_includes_device_accuracy():
    nearby = 27.7 + 0.06  # ~6.7 km north
    assert "location_mismatch" in flags(lat=nearby, accuracy=10)
    assert "location_mismatch" not in flags(lat=nearby, accuracy=3000)


def test_old_photo_presented_as_new_is_flagged():
    assert flags(device_time=T0 + timedelta(days=3))["time_mismatch"] == "hard"
    assert "time_mismatch" not in flags(device_time=T0 + timedelta(hours=5))


def test_missing_sides_skip_comparisons():
    assert flags(lat=None, lon=None, device_time=None) == {}


def test_soft_signals():
    bare = PhotoExif()
    result = flags(exif=bare, exif_time=None, accuracy=2500)
    assert result == {"no_camera_metadata": "soft", "low_accuracy": "soft"}
    edited = PhotoExif(make="Canon", software="Adobe Photoshop 25.0")
    assert flags(exif=edited, exif_time=None)["edited"] == "soft"


def test_identifier_photo_flags_become_hard_flags():
    assert flags(photo=["captive_or_cultivated", "unknown_flag"]) == {
        "captive_or_cultivated": "hard"
    }


def _pattern(seed: int, size=(640, 480)) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (8, 6))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(48)])
    return image.resize(size, Image.Resampling.NEAREST)


def _distance(a: int, b: int) -> int:
    return bin((a ^ b) & (2**64 - 1)).count("1")


def test_dhash_matches_resized_reencoded_copies_but_not_other_photos():
    original = _pattern(1)
    buffer = io.BytesIO()
    original.resize((320, 240)).save(buffer, "JPEG", quality=60)
    copy = Image.open(io.BytesIO(buffer.getvalue()))

    assert _distance(dhash(original), dhash(copy)) <= 5
    assert _distance(dhash(original), dhash(_pattern(2))) > 15
    assert -(2**63) <= dhash(original) < 2**63


def test_flat_images_are_not_used_for_near_duplicate_matching():
    assert not _informative(dhash(Image.new("RGB", (100, 100), (40, 120, 60))))
    assert _informative(dhash(_pattern(3)))


def test_haversine():
    assert haversine_km(27.7172, 85.3240, 22.5726, 88.3639) == pytest.approx(650, rel=0.05)
