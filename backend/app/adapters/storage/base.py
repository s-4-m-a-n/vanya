from abc import ABC, abstractmethod
from pathlib import PurePosixPath


class BlobStorage(ABC):
    """Object storage for observation photos (Cloud Storage on GCP)."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signed_url(self, key: str, expires_in: int = 900) -> str:
        """Time-limited read URL. Photos are never public: they can reveal sensitive sites."""


def validate_key(key: str) -> str:
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid storage key: {key!r}")
    return key
