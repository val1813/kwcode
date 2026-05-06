"""
Frontend-backend file upload interface mismatch task.

Backend: file upload API with multipart form handling
Frontend client: upload service

Bugs:
1. backend.py: validates file size in KB but documents/enforces MB limit
2. backend.py: returns 'fileId' but frontend expects 'file_id'
3. frontend.py: sends Content-Type as 'multipart/form' instead of 'multipart/form-data'
"""

import hashlib
import time
from typing import Optional


MAX_FILE_SIZE_MB = 10
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "application/pdf", "text/plain"}


class UploadError(Exception):
    pass


class FileRecord:
    def __init__(self, file_id: str, filename: str, size: int,
                 content_type: str, checksum: str, uploaded_at: float):
        self.file_id = file_id
        self.filename = filename
        self.size = size
        self.content_type = content_type
        self.checksum = checksum
        self.uploaded_at = uploaded_at


_storage: dict[str, FileRecord] = {}


def upload_file(filename: str, content: bytes, content_type: str,
                now: float = None) -> dict:
    """Process a file upload.

    Returns upload response dict.
    Raises UploadError on validation failure.
    """
    if now is None:
        now = time.time()

    if not filename:
        raise UploadError("Filename is required")

    if content_type not in ALLOWED_TYPES:
        raise UploadError(f"Content type '{content_type}' not allowed")

    # Bug: compares size in bytes to KB limit (should be MB)
    max_bytes = MAX_FILE_SIZE_MB * 1024  # should be * 1024 * 1024
    if len(content) > max_bytes:
        raise UploadError(f"File exceeds {MAX_FILE_SIZE_MB}MB limit")

    checksum = hashlib.md5(content).hexdigest()
    file_id = f"file-{checksum[:8]}-{int(now)}"

    record = FileRecord(
        file_id=file_id,
        filename=filename,
        size=len(content),
        content_type=content_type,
        checksum=checksum,
        uploaded_at=now,
    )
    _storage[file_id] = record

    # Bug: returns 'fileId' (camelCase) but frontend expects 'file_id' (snake_case)
    return {
        "fileId": file_id,
        "filename": filename,
        "size": len(content),
        "checksum": checksum,
        "url": f"/files/{file_id}",
    }


def get_file_info(file_id: str) -> Optional[dict]:
    """Get metadata for an uploaded file."""
    record = _storage.get(file_id)
    if record is None:
        return None
    return {
        "file_id": record.file_id,
        "filename": record.filename,
        "size": record.size,
        "content_type": record.content_type,
        "checksum": record.checksum,
        "url": f"/files/{record.file_id}",
    }


def delete_file(file_id: str) -> bool:
    """Delete a file record. Returns True if deleted."""
    if file_id in _storage:
        del _storage[file_id]
        return True
    return False


def list_files() -> list[dict]:
    """List all uploaded files."""
    return [get_file_info(fid) for fid in _storage]
