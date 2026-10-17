import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from app.adapters.storage.base import BlobStorage, validate_key


class LocalBlobStorage(BlobStorage):
    """Filesystem storage; signed URLs are served by the API's /media route."""

    def __init__(self, root: str, base_url: str, secret: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.secret = secret.encode()

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def signed_url(self, key: str, expires_in: int = 900) -> str:
        expires = int(time.time()) + expires_in
        query = urlencode({"expires": expires, "signature": self.sign(key, expires)})
        return f"{self.base_url}/media/{quote(validate_key(key))}?{query}"

    def sign(self, key: str, expires: int) -> str:
        message = f"{key}:{expires}".encode()
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def verify(self, key: str, expires: int, signature: str) -> bool:
        if expires < time.time():
            return False
        return hmac.compare_digest(self.sign(key, expires), signature)
