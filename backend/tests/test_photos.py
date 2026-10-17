import io
from datetime import datetime

import pytest
from PIL import Image

from app.services.photos import STORED_MAX_PX, InvalidPhoto, process_photo
from tests.images import make_jpeg


def test_extracts_gps_time_and_camera():
    photo = process_photo(make_jpeg(gps=(-33.8568, 151.2153), taken_at="2026:10:01 07:30:00"))
    assert photo.exif.latitude == pytest.approx(-33.8568, abs=1e-4)
    assert photo.exif.longitude == pytest.approx(151.2153, abs=1e-4)
    assert photo.exif.taken_at == datetime(2026, 10, 1, 7, 30)
    assert photo.exif.make == "TestCam"
    assert len(photo.sha256) == 64


def test_stored_copy_is_resized_and_stripped_of_exif():
    photo = process_photo(make_jpeg(size=(4000, 3000), gps=(10.0, 20.0)))
    assert max(photo.width, photo.height) == STORED_MAX_PX
    stored = Image.open(io.BytesIO(photo.jpeg))
    assert stored.format == "JPEG"
    assert len(stored.getexif()) == 0
    assert max(Image.open(io.BytesIO(photo.id_jpeg)).size) <= 1024


def test_photo_without_exif_has_no_location():
    photo = process_photo(make_jpeg(fmt="PNG"))
    assert photo.exif.latitude is None and photo.exif.taken_at is None


def test_heic_is_accepted():
    assert process_photo(make_jpeg(size=(320, 240), fmt="HEIF")).width == 320


@pytest.mark.parametrize(
    "data", [b"not an image", b"", make_jpeg(fmt="GIF")], ids=["garbage", "empty", "gif"]
)
def test_rejects_unsupported_input(data):
    with pytest.raises(InvalidPhoto):
        process_photo(data)
