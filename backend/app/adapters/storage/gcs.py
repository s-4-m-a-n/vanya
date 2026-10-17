import contextlib
from datetime import timedelta

from app.adapters.storage.base import BlobStorage, validate_key


class GcsBlobStorage(BlobStorage):
    """Google Cloud Storage. Requires the `gcp` extra.

    On Cloud Run, V4 signing uses the IAM signBlob API, so the runtime service account
    needs roles/iam.serviceAccountTokenCreator on itself.
    """

    def __init__(self, bucket: str, project: str | None = None) -> None:
        from google.cloud import storage

        self._bucket = storage.Client(project=project).bucket(bucket)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._bucket.blob(validate_key(key)).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes:
        return self._bucket.blob(validate_key(key)).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self._bucket.blob(validate_key(key)).exists()

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        with contextlib.suppress(NotFound):
            self._bucket.blob(validate_key(key)).delete()

    def signed_url(self, key: str, expires_in: int = 900) -> str:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default()
        credentials.refresh(Request())
        return self._bucket.blob(validate_key(key)).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in),
            method="GET",
            service_account_email=getattr(credentials, "service_account_email", None),
            access_token=credentials.token,
        )
