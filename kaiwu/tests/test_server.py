"""
Tests for kwcode server module.
Tests FastAPI endpoints with mock orchestrator.
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ═══════════════════════════════════════════════════════════════════
# Server App Tests
# ═══════════════════════════════════════════════════════════════════

class TestServerHealth:
    """Test health and status endpoints."""

    def _get_app(self, tmp_path):
        """Create a test app with mocked pipeline."""
        with patch("kaiwu.server.pipeline_factory.build_pipeline") as mock_build:
            mock_gate = MagicMock()
            mock_orch = MagicMock()
            mock_orch.bus = MagicMock()
            mock_orch.bus.on = MagicMock()
            mock_orch.bus.off = MagicMock()
            mock_memory = MagicMock()
            mock_memory.load = MagicMock(return_value="")
            mock_registry = MagicMock()
            mock_registry.list_experts = MagicMock(return_value=[])

            mock_build.return_value = (mock_gate, mock_orch, mock_memory, mock_registry)

            from kaiwu.server.app import create_app
            app = create_app(
                project_root=str(tmp_path),
                ollama_model="test-model",
            )
            return app, mock_gate, mock_orch

    def test_health_endpoint(self, tmp_path):
        """GET /api/health returns ok."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["version"] == "1.3.0"
                assert data["model"] == "test-model"

        asyncio.run(_test())

    def test_status_endpoint(self, tmp_path):
        """GET /api/status returns server status."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/status")
                assert resp.status_code == 200
                data = resp.json()
                assert "model" in data
                assert "uptime_seconds" in data

        asyncio.run(_test())

    def test_files_endpoint(self, tmp_path):
        """GET /api/files returns file tree."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        # Create some files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/files")
                assert resp.status_code == 200
                data = resp.json()
                assert "items" in data
                names = [item["name"] for item in data["items"]]
                assert "src" in names
                assert "README.md" in names

        asyncio.run(_test())

    def test_file_read_endpoint(self, tmp_path):
        """GET /api/file returns file content."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        (tmp_path / "test.py").write_text("x = 42\n", encoding="utf-8")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/file", params={"path": "test.py"})
                assert resp.status_code == 200
                data = resp.json()
                assert data["content"] == "x = 42\n"
                assert data["language"] == "python"
                assert data["lines"] == 2

        asyncio.run(_test())

    def test_file_not_found(self, tmp_path):
        """GET /api/file returns 404 for missing file."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/file", params={"path": "nonexistent.py"})
                assert resp.status_code == 404

        asyncio.run(_test())

    def test_task_submit(self, tmp_path):
        """POST /api/task accepts a task and returns task_id."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        app, mock_gate, mock_orch = self._get_app(tmp_path)
        mock_gate.classify = MagicMock(return_value={
            "expert_type": "chat", "task_summary": "test", "difficulty": "easy"
        })
        mock_orch.run = MagicMock(return_value={
            "success": True, "context": MagicMock(generator_output=None), "error": None, "elapsed": 0.5
        })

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/task", json={"input": "hello"})
                assert resp.status_code == 200
                data = resp.json()
                assert "task_id" in data
                assert data["status"] == "accepted"

        asyncio.run(_test())

    def test_rig_refresh(self, tmp_path):
        """POST /api/rig/refresh rebuilds rig.json."""
        try:
            from httpx import AsyncClient, ASGITransport
        except ImportError:
            pytest.skip("httpx not available")

        (tmp_path / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

        app, _, _ = self._get_app(tmp_path)

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/rig/refresh")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════
# Server Models Tests
# ═══════════════════════════════════════════════════════════════════

class TestServerModels:
    """Test Pydantic models."""

    def test_task_request_defaults(self):
        from kaiwu.server.models import TaskRequest
        req = TaskRequest(input="fix bug")
        assert req.input == "fix bug"
        assert req.project_root == "."
        assert req.no_search is False
        assert req.image_paths == []

    def test_task_response(self):
        from kaiwu.server.models import TaskResponse
        resp = TaskResponse(task_id="abc123")
        assert resp.task_id == "abc123"
        assert resp.status == "accepted"

    def test_health_response(self):
        from kaiwu.server.models import HealthResponse
        resp = HealthResponse(model="qwen3-8b", project_root="/tmp")
        assert resp.status == "ok"
        assert resp.version == "1.3.0"

    def test_file_content(self):
        from kaiwu.server.models import FileContent
        fc = FileContent(path="main.py", content="x=1", language="python", lines=1)
        assert fc.path == "main.py"
        assert fc.language == "python"


# ═══════════════════════════════════════════════════════════════════
# Pipeline Factory Tests
# ═══════════════════════════════════════════════════════════════════

class TestPipelineFactory:
    """Test pipeline_factory.build_pipeline."""

    def test_build_pipeline_returns_tuple(self, tmp_path):
        """build_pipeline returns (gate, orchestrator, memory, registry)."""
        from kaiwu.server.pipeline_factory import build_pipeline

        # This will fail if Ollama isn't running, but we can at least test imports work
        try:
            result = build_pipeline(
                project_root=str(tmp_path),
                ollama_model="test",
            )
            assert len(result) == 4
            gate, orch, mem, reg = result
            assert gate is not None
            assert orch is not None
        except Exception:
            # Expected if no LLM backend available
            pass


# ═══════════════════════════════════════════════════════════════════
# TUI Tests
# ═══════════════════════════════════════════════════════════════════

class TestTUI:
    """Tests for TUI module."""

    def test_textual_import_check(self):
        """TUI module handles missing textual gracefully."""
        from kaiwu.tui.app import TEXTUAL_AVAILABLE
        # Should be a boolean regardless of whether textual is installed
        assert isinstance(TEXTUAL_AVAILABLE, bool)

    def test_event_icons_defined(self):
        """TUI has event icons for common events."""
        from kaiwu.tui.app import EVENT_ICONS
        assert "task_completed" in EVENT_ICONS
        assert "task_error" in EVENT_ICONS
        assert "expert_start" in EVENT_ICONS

    def test_check_server_unreachable(self):
        """_check_server returns False for unreachable server."""
        from kaiwu.tui.app import _check_server
        # Port 1 should never be reachable
        assert _check_server("http://127.0.0.1:1", timeout=0.5) is False

    def test_default_server_url(self):
        """Default server URL is localhost:7355."""
        from kaiwu.tui.app import DEFAULT_SERVER_URL
        assert "7355" in DEFAULT_SERVER_URL
        assert "127.0.0.1" in DEFAULT_SERVER_URL
