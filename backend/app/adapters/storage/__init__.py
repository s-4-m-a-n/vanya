from functools import lru_cache

from app.adapters.storage.base import BlobStorage
from app.config import get_settings


@lru_cache
def get_storage() -> BlobStorage:
    settings = get_settings()
    if settings.storage_provider == "gcs":
        from app.adapters.storage.gcs import GcsBlobStorage

        if not settings.gcs_bucket:
            raise RuntimeError("GCS_BUCKET must be set when STORAGE_PROVIDER=gcs")
        return GcsBlobStorage(settings.gcs_bucket, settings.gcp_project_id)

    from app.adapters.storage.local import LocalBlobStorage

    return LocalBlobStorage(
        settings.storage_local_dir, settings.media_base_url, settings.media_url_secret
    )


__all__ = ["BlobStorage", "get_storage"]
