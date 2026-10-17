from urllib.parse import urlsplit

import pytest

from app.adapters.storage import get_storage


@pytest.fixture
def storage():
    return get_storage()


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def test_put_get_delete(storage):
    storage.put("obs/a.jpg", b"jpeg-bytes", "image/jpeg")
    assert storage.exists("obs/a.jpg")
    assert storage.get("obs/a.jpg") == b"jpeg-bytes"
    storage.delete("obs/a.jpg")
    assert not storage.exists("obs/a.jpg")


@pytest.mark.parametrize("key", ["../escape.jpg", "/etc/passwd", "", "a/../../b"])
def test_rejects_unsafe_keys(storage, key):
    with pytest.raises(ValueError):
        storage.put(key, b"x", "image/jpeg")


def test_signed_url_serves_file(client, storage):
    storage.put("obs/b.jpg", b"jpeg-bytes", "image/jpeg")
    res = client.get(_path_and_query(storage.signed_url("obs/b.jpg")))
    assert res.status_code == 200
    assert res.content == b"jpeg-bytes"
    assert res.headers["content-type"] == "image/jpeg"


def test_tampered_or_expired_signature_is_404(client, storage):
    storage.put("obs/c.jpg", b"x", "image/jpeg")
    url = _path_and_query(storage.signed_url("obs/c.jpg"))
    assert client.get(url.replace("obs/c.jpg", "obs/d.jpg")).status_code == 404
    expired = _path_and_query(storage.signed_url("obs/c.jpg", expires_in=-1))
    assert client.get(expired).status_code == 404
