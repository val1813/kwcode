"""Tests for frontend-backend file upload interface consistency."""

import pytest
from backend import upload_file, get_file_info, delete_file, UploadError, _storage
from frontend import UploadClient


def setup_function():
    _storage.clear()


class TestBackendUpload:
    def test_upload_small_file(self):
        content = b"hello world"
        resp = upload_file("test.txt", content, "text/plain", now=1000.0)
        assert "file_id" in resp
        assert resp["filename"] == "test.txt"
        assert resp["size"] == len(content)

    def test_upload_returns_file_id_snake_case(self):
        """Backend must return 'file_id' (snake_case) for frontend compatibility."""
        resp = upload_file("test.txt", b"data", "text/plain", now=1000.0)
        assert "file_id" in resp, "Backend must return 'file_id', not 'fileId'"
        assert "fileId" not in resp

    def test_upload_rejects_disallowed_type(self):
        with pytest.raises(UploadError, match="not allowed"):
            upload_file("script.exe", b"data", "application/exe", now=1000.0)

    def test_upload_rejects_oversized_file(self):
        """File larger than 10MB should be rejected."""
        big_content = b"x" * (11 * 1024 * 1024)  # 11 MB
        with pytest.raises(UploadError, match="limit"):
            upload_file("big.txt", big_content, "text/plain", now=1000.0)

    def test_upload_allows_10mb_file(self):
        """File exactly at 10MB should be accepted."""
        content = b"x" * (10 * 1024 * 1024)
        resp = upload_file("max.txt", content, "text/plain", now=1000.0)
        assert resp["file_id"] is not None

    def test_upload_rejects_empty_filename(self):
        with pytest.raises(UploadError):
            upload_file("", b"data", "text/plain", now=1000.0)

    def test_get_file_info_after_upload(self):
        resp = upload_file("img.png", b"png-data", "image/png", now=1000.0)
        info = get_file_info(resp["file_id"])
        assert info is not None
        assert info["filename"] == "img.png"

    def test_delete_file(self):
        resp = upload_file("del.txt", b"data", "text/plain", now=1000.0)
        fid = resp["file_id"]
        assert delete_file(fid) is True
        assert get_file_info(fid) is None

    def test_checksum_consistent(self):
        content = b"consistent content"
        resp1 = upload_file("a.txt", content, "text/plain", now=1000.0)
        _storage.clear()
        resp2 = upload_file("a.txt", content, "text/plain", now=2000.0)
        assert resp1["checksum"] == resp2["checksum"]


class TestFrontendUploadClient:
    def test_upload_returns_file_id(self):
        client = UploadClient()
        result = client.upload("test.txt", b"hello", "text/plain", now=1000.0)
        assert result["file_id"] is not None

    def test_upload_tracks_locally(self):
        client = UploadClient()
        client.upload("test.txt", b"hello", "text/plain", now=1000.0)
        assert len(client.local_uploads()) == 1

    def test_upload_result_has_url(self):
        client = UploadClient()
        result = client.upload("test.txt", b"hello", "text/plain", now=1000.0)
        assert result["url"] is not None
        assert result["url"].startswith("/files/")

    def test_get_info_after_upload(self):
        client = UploadClient()
        result = client.upload("img.png", b"png-data", "image/png", now=1000.0)
        info = client.get_info(result["file_id"])
        assert info is not None
        assert info["filename"] == "img.png"

    def test_delete_removes_from_local(self):
        client = UploadClient()
        result = client.upload("test.txt", b"hello", "text/plain", now=1000.0)
        fid = result["file_id"]
        client.delete(fid)
        assert len(client.local_uploads()) == 0

    def test_upload_multiple_files(self):
        client = UploadClient()
        client.upload("a.txt", b"aaa", "text/plain", now=1000.0)
        client.upload("b.txt", b"bbb", "text/plain", now=1001.0)
        assert len(client.local_uploads()) == 2
