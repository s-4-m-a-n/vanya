import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.adapters.storage import BlobStorage, get_storage
from app.adapters.storage.local import LocalBlobStorage

# Only mounted when STORAGE_PROVIDER=local; on GCP clients fetch V4 signed URLs from GCS.
router = APIRouter(tags=["media"])


@router.get("/media/{key:path}")
def get_media(
    key: str,
    expires: int,
    signature: str,
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> Response:
    if not isinstance(storage, LocalBlobStorage):
        raise HTTPException(404)
    try:
        if not storage.verify(key, expires, signature) or not storage.exists(key):
            raise HTTPException(404)
        data = storage.get(key)
    except ValueError as exc:
        raise HTTPException(404) from exc
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})
