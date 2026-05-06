"""Frontend upload client."""

from typing import Optional, Callable
from backend import upload_file, get_file_info, delete_file, UploadError


class UploadClient:
    """Frontend client for file upload operations."""

    def __init__(self):
        self._uploads: dict[str, dict] = {}  # local tracking of uploads

    def _build_headers(self, content_type: str) -> dict:
        # Bug: uses 'multipart/form' instead of 'multipart/form-data'
        return {
            "Content-Type": "multipart/form",
            "X-Upload-Content-Type": content_type,
        }

    def upload(self, filename: str, content: bytes,
               content_type: str, now: float = None) -> dict:
        """Upload a file and return the upload result.

        Returns dict with 'file_id', 'url', 'filename', 'size', 'checksum'.
        """
        headers = self._build_headers(content_type)
        # Simulate calling backend upload endpoint
        response = upload_file(filename, content, content_type, now=now)

        # Bug: reads 'file_id' but backend returns 'fileId'
        file_id = response.get("file_id")
        if file_id is None:
            raise UploadError("Backend did not return file_id")

        result = {
            "file_id": file_id,
            "url": response.get("url"),
            "filename": response.get("filename"),
            "size": response.get("size"),
            "checksum": response.get("checksum"),
        }
        self._uploads[file_id] = result
        return result

    def get_info(self, file_id: str) -> Optional[dict]:
        """Get file metadata from backend."""
        return get_file_info(file_id)

    def delete(self, file_id: str) -> bool:
        """Delete a file."""
        ok = delete_file(file_id)
        if ok:
            self._uploads.pop(file_id, None)
        return ok

    def local_uploads(self) -> list[dict]:
        """Return locally tracked uploads."""
        return list(self._uploads.values())
